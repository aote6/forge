"""Priority 3: Failure-classified self-correction regression tests."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from forge.adapters.verification import verify
from forge.failures import (
    FailureClass,
    FailureRecord,
    RepairConstraints,
    build_repair_constraints,
    classify_execution_error,
    classify_verification_result,
    compute_plan_signature,
    is_duplicate_repair,
    repair_attempt_record,
)
from forge.memory.checkpoint import CheckpointStore
from forge.orchestrator.engine import EngineeringOrchestrator
from forge.plan_validator import PlanValidationError, PlanValidator
from forge.protocols.models import (
    ChangeProposal,
    CheckStatus,
    OrchestratorPhase,
    Plan,
    PlanStep,
    RepoContext,
    TaskCheckpoint,
    VerificationRequest,
    VerificationResult,
)
from forge.projections.base import ProjectionManager
from forge.context.snapshot import take_snapshot


def _w(root: Path, rel: str, content: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


class TestClassification(unittest.TestCase):
    def test_receipt_failure(self):
        vres = VerificationResult(
            status=CheckStatus.FAIL,
            receipt_ok=False,
            projection_ok=True,
            failures=["receipt: not provided"],
            evidence={"receipt": "missing"},
        )
        recs = classify_verification_result(vres)
        self.assertTrue(any(r.code == FailureClass.RECEIPT_FAILURE.value for r in recs))

    def test_missing_file(self):
        vres = VerificationResult(
            status=CheckStatus.FAIL,
            receipt_ok=True,
            projection_ok=False,
            failures=["projection: file missing: a.py"],
            evidence={},
        )
        recs = classify_verification_result(vres)
        self.assertEqual(recs[0].code, FailureClass.MISSING_FILE.value)
        self.assertIn("a.py", recs[0].files)

    def test_projection_failure(self):
        vres = VerificationResult(
            status=CheckStatus.FAIL,
            receipt_ok=True,
            projection_ok=False,
            failures=["projection: content mismatch"],
            evidence={},
        )
        recs = classify_verification_result(vres)
        self.assertEqual(recs[0].code, FailureClass.PROJECTION_FAILURE.value)

    def test_build_failure(self):
        vres = VerificationResult(
            status=CheckStatus.FAIL,
            receipt_ok=True,
            projection_ok=True,
            build_ok=False,
            failures=["build: compile failed"],
            evidence={"build_status": "fail", "build_checks": ["compile"]},
        )
        recs = classify_verification_result(vres)
        self.assertEqual(recs[0].code, FailureClass.BUILD_FAILURE.value)

    def test_test_failure(self):
        vres = VerificationResult(
            status=CheckStatus.FAIL,
            receipt_ok=True,
            projection_ok=True,
            build_ok=False,
            failures=["build: tests failed"],
            evidence={"build_status": "fail", "test_failure": True, "failed_files": ["t.py"]},
        )
        recs = classify_verification_result(vres)
        self.assertEqual(recs[0].code, FailureClass.TEST_FAILURE.value)
        self.assertIn("t.py", recs[0].files)

    def test_stale_snapshot_execution(self):
        r = classify_execution_error("STALE_SNAPSHOT: plan snapshot x != current y")
        self.assertEqual(r.code, FailureClass.STALE_SNAPSHOT.value)
        self.assertFalse(r.repairable)

    def test_unknown(self):
        vres = VerificationResult(
            status=CheckStatus.FAIL,
            failures=["something odd happened"],
            evidence={},
        )
        # all checks ok flags default true → falls through to unknown if failures
        recs = classify_verification_result(vres)
        self.assertTrue(recs)
        self.assertEqual(recs[0].code, FailureClass.UNKNOWN_FAILURE.value)


class TestVerifyStructured(unittest.TestCase):
    def test_verify_embeds_structured_failures(self):
        req = VerificationRequest(changed_files=["no_such_file_xyz.py"])
        with tempfile.TemporaryDirectory() as td:
            # no receipt → receipt fail; missing file → missing
            vres = verify(req, project_root=td, hub=MagicMock(
                invoke=MagicMock(return_value=SimpleNamespace(ok=True, data={"status": "pass"}, error=""))
            ), receipt=None)
            self.assertEqual(vres.status, CheckStatus.FAIL)
            sf = (vres.evidence or {}).get("structured_failures") or []
            self.assertTrue(sf)
            codes = {x["code"] for x in sf}
            self.assertIn(FailureClass.RECEIPT_FAILURE.value, codes)


class TestCheckpointRoundTrip(unittest.TestCase):
    def test_failure_history_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            store = CheckpointStore(td)
            frec = FailureRecord(
                code=FailureClass.TEST_FAILURE.value,
                message="tests failed",
                phase="verifying",
                files=["t.py"],
                evidence={"test_failure": True},
            )
            cp = TaskCheckpoint(
                task_id="t_fail",
                phase=OrchestratorPhase.PLANNING.value,
                goal="g",
                extra={
                    "failure_history": [frec.to_dict()],
                    "last_failure": frec.to_dict(),
                    "repair_constraints": build_repair_constraints(frec).to_dict(),
                },
            )
            store.save(cp)
            loaded = store.load("t_fail")
            self.assertIsNotNone(loaded)
            hist = loaded.extra["failure_history"]
            self.assertEqual(hist[0]["code"], FailureClass.TEST_FAILURE.value)
            self.assertEqual(hist[0]["signature"], frec.signature)
            restored = FailureRecord.from_dict(hist[0])
            self.assertEqual(restored.code, frec.code)
            self.assertEqual(restored.files, ["t.py"])


class TestRepairConstraintsDiffer(unittest.TestCase):
    def test_test_vs_syntax_vs_missing(self):
        t = FailureRecord(code=FailureClass.TEST_FAILURE.value, message="t", files=["test_a.py"])
        s = FailureRecord(code=FailureClass.SYNTAX_FAILURE.value, message="s", files=["a.py"])
        m = FailureRecord(code=FailureClass.MISSING_FILE.value, message="m", files=["gone.py"])
        ct = build_repair_constraints(t)
        cs = build_repair_constraints(s)
        cm = build_repair_constraints(m)
        self.assertNotEqual(ct.to_dict(), cs.to_dict())
        self.assertNotEqual(cs.to_dict(), cm.to_dict())
        self.assertIn("gone.py", cm.force_create_files)
        self.assertIn("a.py", cs.must_touch_files)
        self.assertTrue(ct.must_touch_files or ct.required_impact_files)

    def test_validator_enforces_missing_create(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _w(root, "ok.py", "x\n")
            v = PlanValidator(str(root))
            repo = RepoContext(file_tree=["ok.py"])
            rc = RepairConstraints(
                failure_code=FailureClass.MISSING_FILE.value,
                force_create_files=["gone.py"],
                must_touch_files=["gone.py"],
            )
            plan_dict = {
                "goal": "fix",
                "steps": [{
                    "step_id": "s1",
                    "description": "bad modify missing",
                    "target_files": ["gone.py"],
                    "operation_type": "modify",
                    "start_line": 1,
                    "end_line": 1,
                    "new_text": "y",
                }],
            }
            # gone not in tree → structural fail first; use create path
            plan_dict["steps"][0]["operation_type"] = "create_file"
            plan_dict["steps"][0]["content"] = "z\n"
            plan, _ = v.validate(plan_dict, repo, repair_constraints=rc)
            self.assertEqual(plan.steps[0].operation_type, "create_file")

            # modify of forced-create file rejected
            plan_dict2 = {
                "goal": "fix",
                "steps": [{
                    "step_id": "s1",
                    "description": "modify missing",
                    "target_files": ["ok.py"],
                    "operation_type": "modify",
                    "start_line": 1,
                    "end_line": 1,
                    "new_text": "y\n",
                }],
            }
            # must_touch gone.py not satisfied
            with self.assertRaises(PlanValidationError) as cm:
                v.validate(plan_dict2, repo, repair_constraints=rc)
            self.assertIn("must touch", str(cm.exception).lower())

    def test_syntax_must_touch(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _w(root, "a.py", "x\n")
            _w(root, "b.py", "y\n")
            v = PlanValidator(str(root))
            repo = RepoContext(file_tree=["a.py", "b.py"])
            rc = RepairConstraints(
                failure_code=FailureClass.SYNTAX_FAILURE.value,
                must_touch_files=["a.py"],
                required_impact_files=["a.py"],
            )
            bad = {
                "goal": "fix",
                "steps": [{
                    "step_id": "s1",
                    "description": "edit b only",
                    "target_files": ["b.py"],
                    "operation_type": "modify",
                    "start_line": 1,
                    "end_line": 1,
                    "new_text": "z\n",
                }],
            }
            with self.assertRaises(PlanValidationError):
                v.validate(bad, repo, repair_constraints=rc)


class TestDuplicateRepair(unittest.TestCase):
    def test_same_failure_same_plan_detected(self):
        plan = Plan(
            plan_id="p1",
            goal="g",
            steps=[PlanStep(step_id="s1", description="d", target_files=["a.py"], operation_type="modify", start_line=1, end_line=1)],
        )
        fail = FailureRecord(code=FailureClass.TEST_FAILURE.value, message="t", files=["a.py"])
        hist = [repair_attempt_record(fail, plan)]
        self.assertTrue(is_duplicate_repair(fail, plan, hist))

    def test_different_failure_allows_retry(self):
        plan = Plan(
            plan_id="p1",
            goal="g",
            steps=[PlanStep(step_id="s1", description="d", target_files=["a.py"], operation_type="modify")],
        )
        fail_a = FailureRecord(code=FailureClass.TEST_FAILURE.value, message="A", files=["a.py"])
        fail_b = FailureRecord(code=FailureClass.TEST_FAILURE.value, message="B", files=["b.py"])
        hist = [repair_attempt_record(fail_a, plan)]
        self.assertFalse(is_duplicate_repair(fail_b, plan, hist))


class TestStaleStillZeroMutation(unittest.TestCase):
    def test_stale_records_failure_and_no_execute(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _w(root, "a.py", "v1\n")
            snap = take_snapshot(str(root))
            plan = Plan(
                plan_id="pl",
                goal="g",
                snapshot_id=snap.snapshot_id,
                tree_hash=snap.tree_hash,
                steps=[],
            )
            _w(root, "a.py", "v2\n")
            orch = EngineeringOrchestrator(
                project_root=str(root),
                world=MagicMock(),
                projections=ProjectionManager(),
                planner=MagicMock(),
                hub=MagicMock(),
                checkpoint_store=CheckpointStore(str(root)),
            )
            orch.execution = MagicMock()
            orch.execution.execute_proposal = MagicMock()
            orch.checkpoint = TaskCheckpoint(
                task_id="t_stale",
                phase=OrchestratorPhase.EXECUTING.value,
                plan=plan,
                goal="g",
                snapshot_id=plan.snapshot_id,
                change_proposals=[
                    ChangeProposal(proposal_id="p1", plan_id="pl", target_files=["a.py"], operations=[{"type": "create_file", "target_files": ["x.py"], "content": "1"}])
                ],
            )
            orch.phase = OrchestratorPhase.EXECUTING
            orch._step()
            orch.execution.execute_proposal.assert_not_called()
            self.assertEqual(orch.phase, OrchestratorPhase.FAILED)
            hist = orch.checkpoint.extra.get("failure_history") or []
            self.assertTrue(hist)
            self.assertEqual(hist[-1]["code"], FailureClass.STALE_SNAPSHOT.value)


class TestOrchestratorVerifyStoresFailure(unittest.TestCase):
    def test_verify_fail_writes_history(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _w(root, "a.py", "x\n")
            orch = EngineeringOrchestrator(
                project_root=str(root),
                world=MagicMock(get_version=MagicMock(return_value=1)),
                projections=ProjectionManager(),
                planner=MagicMock(),
                hub=MagicMock(),
                checkpoint_store=CheckpointStore(str(root)),
            )
            snap = take_snapshot(str(root))
            orch.checkpoint = TaskCheckpoint(
                task_id="t_v",
                phase=OrchestratorPhase.VERIFYING.value,
                plan=Plan(plan_id="p", goal="g", steps=[PlanStep(step_id="s1", description="d", target_files=["a.py"], operation_type="modify")], snapshot_id=snap.snapshot_id),
                goal="g",
                snapshot_id=snap.snapshot_id,
                extra={"last_receipt": {"tx_id": 1, "version": 1}},
            )
            orch.phase = OrchestratorPhase.VERIFYING
            # patch verification_verify
            import forge.orchestrator.engine as eng
            bad = VerificationResult(
                status=CheckStatus.FAIL,
                receipt_ok=True,
                projection_ok=False,
                failures=["projection: file missing: a.py"],
                evidence={},
            )
            # ensure structured via classify path
            from forge.failures import classify_verification_result
            bad.evidence["structured_failures"] = [f.to_dict() for f in classify_verification_result(bad)]
            orig = eng.verification_verify
            eng.verification_verify = lambda *a, **k: bad
            try:
                orch._step()
            finally:
                eng.verification_verify = orig
            hist = orch.checkpoint.extra.get("failure_history") or []
            self.assertTrue(hist)
            self.assertEqual(hist[-1]["code"], FailureClass.MISSING_FILE.value)
            self.assertEqual(orch.phase, OrchestratorPhase.PLANNING)


if __name__ == "__main__":
    unittest.main()

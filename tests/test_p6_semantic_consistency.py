"""Priority 6: Plan → VERIFY semantic consistency + pre-execution snapshot."""
from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from forge.context.index import RepositoryIndex
from forge.context.planning import (
    collect_plan_target_files,
    content_hashes,
    derive_expected_symbols_for_plan,
    plan_expected_symbols_map,
)
from forge.context.snapshot import take_snapshot
from forge.failures import FailureClass, classify_verification_result
from forge.memory.checkpoint import CheckpointStore
from forge.orchestrator.engine import EngineeringOrchestrator
from forge.adapters.verification import verify
from forge.protocols.models import (
    ChangeProposal,
    CheckStatus,
    ExecutionResult,
    OrchestratorPhase,
    Plan,
    PlanStep,
    TaskCheckpoint,
    VerificationRequest,
)
from forge.projections.base import ProjectionManager
from forge.verification.outcome import verify_plan_outcomes


def _w(root: Path, rel: str, content: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def _hub_pass():
    return MagicMock(
        invoke=MagicMock(
            return_value=SimpleNamespace(
                ok=True, data={"status": "pass", "executed_checks": []}, error=""
            )
        )
    )


class TestExpectedSymbolsDerivation(unittest.TestCase):
    def test_modify_includes_definitions(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _w(
                root,
                "foo.py",
                "class Foo:\n    def bar(self):\n        pass\n\ndef helper():\n    pass\n",
            )
            idx = RepositoryIndex.build(str(root))
            plan = Plan(
                plan_id="p",
                goal="g",
                steps=[
                    PlanStep(
                        step_id="s1",
                        description="m",
                        target_files=["foo.py"],
                        operation_type="modify",
                    )
                ],
            )
            derive_expected_symbols_for_plan(plan, idx)
            syms = plan.steps[0].expected_symbols
            self.assertIn("Foo", syms)
            self.assertIn("Foo.bar", syms)
            self.assertIn("helper", syms)
            self.assertEqual(syms, sorted(syms))

    def test_create_exemption(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _w(root, "a.py", "def f():\n    pass\n")
            idx = RepositoryIndex.build(str(root))
            plan = Plan(
                plan_id="p",
                goal="g",
                steps=[
                    PlanStep(
                        step_id="s1",
                        description="c",
                        target_files=["new.py"],
                        operation_type="create_file",
                        content="x\n",
                    )
                ],
            )
            derive_expected_symbols_for_plan(plan, idx)
            self.assertEqual(plan.steps[0].expected_symbols, [])

    def test_delete_exemption(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _w(root, "a.py", "def f():\n    pass\n")
            idx = RepositoryIndex.build(str(root))
            plan = Plan(
                plan_id="p",
                goal="g",
                steps=[
                    PlanStep(
                        step_id="s1",
                        description="d",
                        target_files=["a.py"],
                        operation_type="delete_file",
                    )
                ],
            )
            derive_expected_symbols_for_plan(plan, idx)
            self.assertEqual(plan.steps[0].expected_symbols, [])

    def test_multi_file_union_deterministic(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _w(root, "a.py", "def alpha():\n    pass\n")
            _w(root, "b.py", "def beta():\n    pass\n")
            idx = RepositoryIndex.build(str(root))
            plan = Plan(
                plan_id="p",
                goal="g",
                steps=[
                    PlanStep(
                        step_id="s1",
                        description="m",
                        target_files=["b.py", "a.py"],
                        operation_type="modify",
                    )
                ],
            )
            derive_expected_symbols_for_plan(plan, idx)
            a = list(plan.steps[0].expected_symbols)
            derive_expected_symbols_for_plan(plan, idx)
            b = list(plan.steps[0].expected_symbols)
            self.assertEqual(a, b)
            self.assertIn("alpha", a)
            self.assertIn("beta", a)
            self.assertEqual(a, sorted(a))


class TestPreExecutionSnapshot(unittest.TestCase):
    def test_content_hashes_sha256(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            body = b"hello world\n"
            _w(root, "a.py", body.decode())
            h = content_hashes(str(root), ["a.py", "missing.py"])
            self.assertEqual(h["a.py"], hashlib.sha256(body).hexdigest())
            self.assertEqual(h["missing.py"], "")

    def test_engine_sets_pre_snapshot_once(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _w(root, "a.py", "v1\n")
            snap = take_snapshot(str(root))
            plan = Plan(
                plan_id="pl",
                goal="g",
                snapshot_id=snap.snapshot_id,
                tree_hash=snap.tree_hash,
                steps=[
                    PlanStep(
                        step_id="s1",
                        description="m",
                        target_files=["a.py"],
                        operation_type="modify",
                        start_line=1,
                        end_line=1,
                        new_text="v2\n",
                    )
                ],
            )
            store = CheckpointStore(str(root))
            orch = EngineeringOrchestrator(
                project_root=str(root),
                world=MagicMock(),
                projections=ProjectionManager(),
                planner=MagicMock(),
                hub=MagicMock(),
                checkpoint_store=store,
            )
            orch.execution = MagicMock()
            orch.execution.execute_proposal = MagicMock(
                return_value=ExecutionResult(
                    proposal_id="p1", success=True, files=["a.py"]
                )
            )
            orch.checkpoint = TaskCheckpoint(
                task_id="t_pre",
                phase=OrchestratorPhase.EXECUTING.value,
                plan=plan,
                goal="g",
                snapshot_id=plan.snapshot_id,
                change_proposals=[
                    ChangeProposal(
                        proposal_id="p1",
                        plan_id="pl",
                        target_files=["a.py"],
                        operations=[{"type": "modify"}],
                    )
                ],
            )
            orch.phase = OrchestratorPhase.EXECUTING
            orch._step()
            pre = orch.checkpoint.extra.get("pre_execution_snapshot")
            self.assertIsNotNone(pre)
            self.assertIn("a.py", pre)
            # second execute attempt in same checkpoint must not overwrite
            first = dict(pre)
            orch.phase = OrchestratorPhase.EXECUTING
            orch.checkpoint.completed_steps = []
            orch.checkpoint.change_proposals = [
                ChangeProposal(
                    proposal_id="p2",
                    plan_id="pl",
                    target_files=["a.py"],
                    operations=[{"type": "modify"}],
                )
            ]
            # mutate file so new hash would differ if recomputed
            _w(root, "a.py", "v2\n")
            orch._step()
            self.assertEqual(orch.checkpoint.extra["pre_execution_snapshot"], first)

    def test_checkpoint_roundtrip_preserves_pre_snapshot(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            store = CheckpointStore(str(root))
            cp = TaskCheckpoint(
                task_id="t_rt",
                phase=OrchestratorPhase.EXECUTING.value,
                goal="g",
                extra={
                    "pre_execution_snapshot": {
                        "a.py": "abc123",
                    }
                },
            )
            store.save(cp)
            loaded = store.load("t_rt")
            self.assertEqual(
                loaded.extra["pre_execution_snapshot"], {"a.py": "abc123"}
            )


class TestModifyNoop(unittest.TestCase):
    def test_noop_fails(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            body = "def f():\n    return 1\n"
            _w(root, "a.py", body)
            pre = content_hashes(str(root), ["a.py"])
            plan = Plan(
                plan_id="p",
                goal="g",
                steps=[
                    PlanStep(
                        step_id="s1",
                        description="m",
                        target_files=["a.py"],
                        operation_type="modify",
                        new_text=body,
                    )
                ],
            )
            issues = verify_plan_outcomes(
                str(root), plan=plan, pre_snapshot=pre
            )
            self.assertTrue(any(i.code == "MODIFY_NOOP" for i in issues))
            noop = next(i for i in issues if i.code == "MODIFY_NOOP")
            self.assertEqual(noop.evidence.get("reason"), "modify_noop")
            self.assertEqual(noop.evidence.get("pre_hash"), pre["a.py"])

    def test_actual_change_passes_noop_check(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _w(root, "a.py", "def f():\n    return 1\n")
            pre = content_hashes(str(root), ["a.py"])
            _w(root, "a.py", "def f():\n    return 2\n")
            plan = Plan(
                plan_id="p",
                goal="g",
                steps=[
                    PlanStep(
                        step_id="s1",
                        description="m",
                        target_files=["a.py"],
                        operation_type="modify",
                        new_text="def f():\n    return 2\n",
                    )
                ],
            )
            issues = verify_plan_outcomes(
                str(root), plan=plan, pre_snapshot=pre
            )
            self.assertFalse(any(i.code == "MODIFY_NOOP" for i in issues))


class TestVerifyIntegration(unittest.TestCase):
    def test_verify_uses_plan_expected_symbols(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _w(root, "a.py", "def keep():\n    return 1\n")
            idx = RepositoryIndex.build(str(root))
            plan = Plan(
                plan_id="p",
                goal="g",
                steps=[
                    PlanStep(
                        step_id="s1",
                        description="m",
                        target_files=["a.py"],
                        operation_type="modify",
                        new_text="def keep():\n    return 1\n",
                    )
                ],
            )
            derive_expected_symbols_for_plan(plan, idx)
            # wipe symbol so outcome should fail
            _w(root, "a.py", "x = 1\n")
            pre = {"a.py": "deadbeef"}  # different so no-op not the only failure
            req = VerificationRequest(changed_files=["a.py"])
            vres = verify(
                req,
                project_root=str(root),
                hub=_hub_pass(),
                receipt={"tx_id": 1, "version": 1},
                plan=plan,
                pre_snapshot=pre,
                skip_build=True,
            )
            self.assertEqual(vres.status, CheckStatus.FAIL)
            issues = (vres.evidence.get("outcome") or {}).get("issues") or []
            self.assertTrue(any(i.get("code") == "SYMBOL_MISSING" for i in issues))

    def test_noop_classified_into_failure_record(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            body = "def f():\n    return 1\n"
            _w(root, "a.py", body)
            pre = content_hashes(str(root), ["a.py"])
            plan = Plan(
                plan_id="p",
                goal="g",
                steps=[
                    PlanStep(
                        step_id="s1",
                        description="m",
                        target_files=["a.py"],
                        operation_type="modify",
                        new_text=body,
                        expected_symbols=["f"],
                    )
                ],
            )
            req = VerificationRequest(changed_files=["a.py"])
            vres = verify(
                req,
                project_root=str(root),
                hub=_hub_pass(),
                receipt={"tx_id": 1, "version": 1},
                plan=plan,
                pre_snapshot=pre,
                skip_build=True,
            )
            self.assertEqual(vres.status, CheckStatus.FAIL)
            sf = (vres.evidence or {}).get("structured_failures") or []
            codes = {x["code"] for x in sf}
            self.assertIn(FailureClass.EXECUTION_FAILURE.value, codes)

    def test_delete_still_absent_ok(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            plan = Plan(
                plan_id="p",
                goal="g",
                steps=[
                    PlanStep(
                        step_id="s1",
                        description="d",
                        target_files=["gone.py"],
                        operation_type="delete_file",
                    )
                ],
            )
            issues = verify_plan_outcomes(str(root), plan=plan, pre_snapshot={})
            self.assertEqual(issues, [])

    def test_create_exists_ok(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _w(root, "new.py", "x = 1\n")
            plan = Plan(
                plan_id="p",
                goal="g",
                steps=[
                    PlanStep(
                        step_id="s1",
                        description="c",
                        target_files=["new.py"],
                        operation_type="create_file",
                        content="x = 1\n",
                    )
                ],
            )
            issues = verify_plan_outcomes(str(root), plan=plan)
            self.assertEqual(issues, [])


class TestPlanExpectedMap(unittest.TestCase):
    def test_map_from_steps(self):
        plan = Plan(
            plan_id="p",
            goal="g",
            steps=[
                PlanStep(
                    step_id="s1",
                    description="m",
                    target_files=["a.py"],
                    operation_type="modify",
                    expected_symbols=["Foo", "helper"],
                ),
                PlanStep(
                    step_id="s2",
                    description="c",
                    target_files=["b.py"],
                    operation_type="create_file",
                    expected_symbols=["should_ignore"],
                ),
            ],
        )
        m = plan_expected_symbols_map(plan)
        self.assertEqual(m.get("a.py"), ["Foo", "helper"])
        self.assertNotIn("b.py", m)


if __name__ == "__main__":
    unittest.main()

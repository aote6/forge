"""Priority 5: Engineering Verification & Outcome Reliability."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from forge.adapters.verification import verify
from forge.failures import FailureClass, classify_verification_result
from forge.protocols.models import (
    CheckStatus,
    ExecutionResult,
    Plan,
    PlanStep,
    VerificationRequest,
    VerificationResult,
)
from forge.verification.outcome import (
    verify_outcomes,
    verify_plan_outcomes,
    verify_python_syntax,
)


def _w(root: Path, rel: str, content: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


class TestSyntaxVerification(unittest.TestCase):
    def test_valid_python_ok(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _w(root, "ok.py", "def f():\n    return 1\n")
            issues = verify_python_syntax(str(root), ["ok.py"])
            self.assertEqual(issues, [])

    def test_syntax_error_detected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _w(root, "bad.py", "def f(\n")
            issues = verify_python_syntax(str(root), ["bad.py"])
            self.assertTrue(issues)
            self.assertEqual(issues[0].code, "SYNTAX")
            self.assertIn("bad.py", issues[0].files)

    def test_non_python_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _w(root, "x.txt", "not python {{{")
            issues = verify_python_syntax(str(root), ["x.txt"])
            self.assertEqual(issues, [])


class TestPlanOutcomes(unittest.TestCase):
    def test_create_actually_happened(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _w(root, "new.py", "x = 1\n")
            plan = Plan(
                plan_id="p",
                goal="g",
                steps=[
                    PlanStep(
                        step_id="s1",
                        description="create",
                        target_files=["new.py"],
                        operation_type="create_file",
                        content="x = 1\n",
                    )
                ],
            )
            issues = verify_plan_outcomes(str(root), plan=plan)
            self.assertEqual(issues, [])

    def test_create_missing_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            plan = Plan(
                plan_id="p",
                goal="g",
                steps=[
                    PlanStep(
                        step_id="s1",
                        description="create",
                        target_files=["gone.py"],
                        operation_type="create_file",
                        content="x\n",
                    )
                ],
            )
            issues = verify_plan_outcomes(str(root), plan=plan)
            self.assertTrue(any(i.code == "CREATE_MISSING" for i in issues))

    def test_delete_actually_happened(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            # file absent = delete succeeded
            plan = Plan(
                plan_id="p",
                goal="g",
                steps=[
                    PlanStep(
                        step_id="s1",
                        description="del",
                        target_files=["old.py"],
                        operation_type="delete_file",
                    )
                ],
            )
            issues = verify_plan_outcomes(str(root), plan=plan)
            self.assertEqual(issues, [])

    def test_delete_still_present_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _w(root, "old.py", "x\n")
            plan = Plan(
                plan_id="p",
                goal="g",
                steps=[
                    PlanStep(
                        step_id="s1",
                        description="del",
                        target_files=["old.py"],
                        operation_type="delete_file",
                    )
                ],
            )
            issues = verify_plan_outcomes(str(root), plan=plan)
            self.assertTrue(any(i.code == "DELETE_STILL_PRESENT" for i in issues))

    def test_modify_target_exists(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _w(root, "a.py", "def f():\n    return 2\n")
            plan = Plan(
                plan_id="p",
                goal="g",
                steps=[
                    PlanStep(
                        step_id="s1",
                        description="mod",
                        target_files=["a.py"],
                        operation_type="modify",
                        old_text="def f():\n    return 1\n",
                        new_text="def f():\n    return 2\n",
                    )
                ],
            )
            issues = verify_plan_outcomes(str(root), plan=plan)
            self.assertEqual(issues, [])

    def test_modify_missing_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            plan = Plan(
                plan_id="p",
                goal="g",
                steps=[
                    PlanStep(
                        step_id="s1",
                        description="mod",
                        target_files=["missing.py"],
                        operation_type="modify",
                        new_text="x\n",
                    )
                ],
            )
            issues = verify_plan_outcomes(str(root), plan=plan)
            self.assertTrue(any(i.code == "MODIFY_MISSING" for i in issues))

    def test_unexpected_file_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _w(root, "a.py", "x\n")
            plan = Plan(
                plan_id="p",
                goal="g",
                steps=[
                    PlanStep(
                        step_id="s1",
                        description="mod",
                        target_files=["a.py"],
                        operation_type="modify",
                        new_text="x\n",
                    )
                ],
            )
            er = ExecutionResult(
                proposal_id="p1",
                success=True,
                files=["a.py", "surprise.py"],
            )
            issues = verify_plan_outcomes(
                str(root), plan=plan, execution_results=[er]
            )
            self.assertTrue(any(i.code == "UNEXPECTED_FILE" for i in issues))
            self.assertIn("surprise.py", issues[-1].files)

    def test_expected_symbol_missing_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _w(root, "a.py", "def other():\n    pass\n")
            issues = verify_plan_outcomes(
                str(root),
                plan=None,
                expected_symbols={"a.py": ["Foo"]},
            )
            self.assertTrue(any(i.code == "SYMBOL_MISSING" for i in issues))

    def test_expected_symbol_present_ok(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _w(root, "a.py", "class Foo:\n    pass\n")
            issues = verify_plan_outcomes(
                str(root),
                expected_symbols={"a.py": ["Foo"]},
            )
            self.assertEqual(issues, [])


class TestVerifyOutcomesBundle(unittest.TestCase):
    def test_bundle_flags_syntax_and_outcome(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _w(root, "bad.py", "def (\n")
            plan = Plan(
                plan_id="p",
                goal="g",
                steps=[
                    PlanStep(
                        step_id="s1",
                        description="c",
                        target_files=["missing.py"],
                        operation_type="create_file",
                        content="x\n",
                    )
                ],
            )
            result = verify_outcomes(
                str(root),
                plan=plan,
                changed_files=["bad.py", "missing.py"],
            )
            self.assertFalse(result["syntax_ok"])
            self.assertFalse(result["outcome_ok"])
            self.assertTrue(result["issues"])


class TestVerifyAdapterIntegration(unittest.TestCase):
    def test_golden_path_pass(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _w(root, "a.py", "def f():\n    return 1\n")
            plan = Plan(
                plan_id="p",
                goal="g",
                steps=[
                    PlanStep(
                        step_id="s1",
                        description="mod",
                        target_files=["a.py"],
                        operation_type="modify",
                        new_text="def f():\n    return 1\n",
                    )
                ],
            )
            req = VerificationRequest(changed_files=["a.py"])
            vres = verify(
                req,
                project_root=str(root),
                receipt={"tx_id": 1, "version": 1},
                plan=plan,
                execution_results=[
                    ExecutionResult(proposal_id="p1", success=True, files=["a.py"])
                ],
                skip_build=False,
            )
            self.assertEqual(vres.status, CheckStatus.PASS)
            self.assertTrue(vres.evidence.get("outcome_ok"))
            self.assertTrue(vres.evidence.get("syntax_ok"))
            self.assertIn("outcome", vres.executed_checks)

    def test_syntax_failure_classified(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _w(root, "bad.py", "def (\n")
            plan = Plan(
                plan_id="p",
                goal="g",
                steps=[
                    PlanStep(
                        step_id="s1",
                        description="mod",
                        target_files=["bad.py"],
                        operation_type="modify",
                        new_text="def (\n",
                    )
                ],
            )
            req = VerificationRequest(changed_files=["bad.py"])
            vres = verify(
                req,
                project_root=str(root),
                receipt={"tx_id": 1, "version": 1},
                plan=plan,
            )
            self.assertEqual(vres.status, CheckStatus.FAIL)
            sf = (vres.evidence or {}).get("structured_failures") or []
            codes = {x["code"] for x in sf}
            self.assertIn(FailureClass.SYNTAX_FAILURE.value, codes)
            # files localized
            syn = next(x for x in sf if x["code"] == FailureClass.SYNTAX_FAILURE.value)
            self.assertIn("bad.py", syn["files"])

    def test_create_missing_classified(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            plan = Plan(
                plan_id="p",
                goal="g",
                steps=[
                    PlanStep(
                        step_id="s1",
                        description="c",
                        target_files=["nope.py"],
                        operation_type="create_file",
                        content="x\n",
                    )
                ],
            )
            req = VerificationRequest(changed_files=["nope.py"])
            vres = verify(
                req,
                project_root=str(root),
                receipt={"tx_id": 1, "version": 1},
                plan=plan,
                skip_build=True,
            )
            self.assertEqual(vres.status, CheckStatus.FAIL)
            sf = (vres.evidence or {}).get("structured_failures") or []
            self.assertTrue(sf)

    def test_unexpected_file_fails(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _w(root, "a.py", "x\n")
            plan = Plan(
                plan_id="p",
                goal="g",
                steps=[
                    PlanStep(
                        step_id="s1",
                        description="m",
                        target_files=["a.py"],
                        operation_type="modify",
                        new_text="x\n",
                    )
                ],
            )
            req = VerificationRequest(changed_files=["a.py"])
            vres = verify(
                req,
                project_root=str(root),
                receipt={"tx_id": 1, "version": 1},
                plan=plan,
                execution_results=[
                    ExecutionResult(
                        proposal_id="p1", success=True, files=["a.py", "leak.py"]
                    )
                ],
                skip_build=True,
            )
            self.assertEqual(vres.status, CheckStatus.FAIL)
            self.assertFalse(vres.evidence.get("outcome_ok"))

    def test_build_evidence_normalized(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _w(root, "tests/test_fail.py", "def test_f():\n    assert False\n")
            req = VerificationRequest(changed_files=["tests/test_fail.py"])
            vres = verify(
                req,
                project_root=str(root),
                receipt={"tx_id": 1, "version": 1},
            )
            self.assertEqual(vres.status, CheckStatus.FAIL)
            be = vres.evidence.get("build_evidence") or {}
            self.assertEqual(be.get("exit_code"), 1)
            combined = (be.get("stdout_excerpt") or "") + (be.get("stderr_excerpt") or "")
            self.assertIn("test_fail", combined)
            sf = (vres.evidence or {}).get("structured_failures") or []
            codes = {x["code"] for x in sf}
            self.assertIn(FailureClass.TEST_FAILURE.value, codes)


class TestClassifierConsumesOutcome(unittest.TestCase):
    def test_syntax_from_evidence(self):
        vres = VerificationResult(
            status=CheckStatus.FAIL,
            receipt_ok=True,
            projection_ok=True,
            failures=["syntax: bad.py: invalid syntax (line 1)"],
            evidence={
                "syntax_ok": False,
                "outcome_ok": True,
                "outcome": {
                    "issues": [
                        {
                            "code": "SYNTAX",
                            "message": "syntax: bad.py: invalid",
                            "files": ["bad.py"],
                            "evidence": {},
                        }
                    ]
                },
            },
        )
        recs = classify_verification_result(vres)
        self.assertTrue(any(r.code == FailureClass.SYNTAX_FAILURE.value for r in recs))
        syn = next(r for r in recs if r.code == FailureClass.SYNTAX_FAILURE.value)
        self.assertIn("bad.py", syn.files)


if __name__ == "__main__":
    unittest.main()

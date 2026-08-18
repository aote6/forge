"""Priority 8: Test / Verification Target Selection."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from forge.adapters.verification import verify
from forge.context.index import RepositoryIndex
from forge.context.planning import compute_obligations
from forge.context.testing import (
    extract_failed_tests_from_history,
    select_verification_targets,
)
from forge.failures import FailureClass, classify_verification_result
from forge.protocols.models import (
    CheckStatus,
    VerificationRequest,
    VerificationResult,
)


def _w(root: Path, rel: str, content: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def _fixture(root: Path) -> None:
    _w(root, "core.py", "def calculate(x):\n    return x + 1\n")
    _w(
        root,
        "service.py",
        "from core import calculate\n\ndef run():\n    return calculate(1)\n",
    )
    _w(
        root,
        "tests/test_core.py",
        "from core import calculate\n\ndef test_calculate():\n    assert calculate(1) == 2\n",
    )
    _w(root, "tests/test_unrelated.py", "def test_noise():\n    assert True\n")
    _w(root, "unrelated.py", "def noise():\n    return 0\n")


class TestSelectTargets(unittest.TestCase):
    def test_direct_caller_selects_test(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _fixture(root)
            idx = RepositoryIndex.build(str(root))
            obs = compute_obligations(idx, task="修改 calculate 的 API")
            sel = select_verification_targets(
                idx, obligations=obs, impact_files=["core.py", "service.py"]
            )
            self.assertIn("tests/test_core.py", sel["test_files"])
            self.assertFalse(sel["empty"])
            req_files = {r["file"] for r in sel["required"]}
            self.assertIn("tests/test_core.py", req_files)

    def test_module_importer(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _w(root, "pkg_mod.py", "VALUE = 1\n")
            _w(
                root,
                "tests/test_pkg.py",
                "import pkg_mod\n\ndef test_v():\n    assert pkg_mod.VALUE == 1\n",
            )
            idx = RepositoryIndex.build(str(root))
            sel = select_verification_targets(
                idx, obligations=[], impact_files=["pkg_mod.py"]
            )
            self.assertIn("tests/test_pkg.py", sel["test_files"])

    def test_no_relation_no_fake(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _w(root, "a.py", "def f():\n    pass\n")
            _w(root, "tests/test_other.py", "def test_x():\n    assert 1\n")
            idx = RepositoryIndex.build(str(root))
            sel = select_verification_targets(
                idx, obligations=[], impact_files=["a.py"]
            )
            # may get advisory same-name only if tests/test_a.py exists — it doesn't
            self.assertNotIn("tests/test_other.py", sel["test_files"])

    def test_same_name_advisory(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _w(root, "foo.py", "def f():\n    pass\n")
            _w(root, "tests/test_foo.py", "def test_f():\n    assert True\n")
            idx = RepositoryIndex.build(str(root))
            sel = select_verification_targets(
                idx, obligations=[], impact_files=["foo.py"], project_root=str(root)
            )
            self.assertIn("tests/test_foo.py", sel["test_files"])
            adv = {a["file"] for a in sel["advisory"]}
            # if no direct ref, same-name is advisory
            if "tests/test_foo.py" not in {r["file"] for r in sel["required"]}:
                self.assertIn("tests/test_foo.py", adv)

    def test_prior_failed_forced(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _w(root, "a.py", "x = 1\n")
            idx = RepositoryIndex.build(str(root))
            sel = select_verification_targets(
                idx,
                obligations=[],
                impact_files=["a.py"],
                failed_tests=["tests/test_core.py::test_calculate"],
            )
            self.assertIn("tests/test_core.py", sel["test_files"])
            self.assertTrue(sel["forced_failed"])

    def test_empty_obligations_no_fake(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _w(root, "a.py", "x = 1\n")
            idx = RepositoryIndex.build(str(root))
            sel = select_verification_targets(idx, obligations=[], impact_files=[])
            self.assertTrue(sel["empty"])
            self.assertEqual(sel["test_files"], [])

    def test_deterministic(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _fixture(root)
            idx = RepositoryIndex.build(str(root))
            obs = compute_obligations(idx, task="修改 calculate")
            a = select_verification_targets(idx, obligations=obs, impact_files=["core.py"])
            b = select_verification_targets(idx, obligations=obs, impact_files=["core.py"])
            self.assertEqual(a, b)


class TestHistoryExtract(unittest.TestCase):
    def test_extract_failed_tests(self):
        hist = [
            {
                "code": "TEST_FAILURE",
                "files": ["tests/test_core.py"],
                "evidence": {
                    "failed_tests": ["tests/test_core.py::test_calculate"],
                    "build_evidence": {"failed_tests": ["tests/test_core.py::test_other"]},
                },
            }
        ]
        got = extract_failed_tests_from_history(hist)
        self.assertIn("tests/test_core.py::test_calculate", got)
        self.assertIn("tests/test_core.py::test_other", got)
        self.assertIn("tests/test_core.py", got)


class TestVerifyAdapterTargets(unittest.TestCase):
    def test_selected_targets_run_locally(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _w(root, "a.py", "def f():\n    return 1\n")
            _w(root, "tests/test_a.py", "def test_ok():\n    assert True\n")
            targets = {
                "test_files": ["tests/test_a.py"],
                "required": [{"file": "tests/test_a.py", "reason": "direct_ref"}],
                "advisory": [],
                "forced_failed": [],
                "reasons": {"tests/test_a.py": ["direct_ref"]},
                "empty": False,
            }
            req = VerificationRequest(changed_files=["a.py"])
            vres = verify(
                req,
                project_root=str(root),
                receipt={"tx_id": 1, "version": 1},
                test_targets=targets,
                skip_build=False,
            )
            self.assertEqual(vres.status, CheckStatus.PASS)
            self.assertIn("test_selection", vres.evidence)
            self.assertIn("pytest", vres.evidence.get("build_checks") or [])
            be = vres.evidence.get("build_evidence") or {}
            self.assertEqual(be.get("exit_code"), 0)

    def test_empty_targets_passes_without_test_run(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _w(root, "a.py", "x = 1\n")
            req = VerificationRequest(changed_files=["a.py"])
            vres = verify(
                req,
                project_root=str(root),
                receipt={"tx_id": 1, "version": 1},
                test_targets={"test_files": [], "empty": True, "required": [], "advisory": [], "forced_failed": [], "reasons": {}},
            )
            self.assertEqual(vres.status, CheckStatus.PASS)
            # no selected tests → no pytest run, no failed tests
            be = vres.evidence.get("build_evidence") or {}
            self.assertEqual(be.get("failed_tests"), [])

    def test_selected_test_failure_classified(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _w(root, "a.py", "def f():\n    return 1\n")
            _w(root, "tests/test_a.py", "def test_f():\n    assert False\n")
            targets = {
                "test_files": ["tests/test_a.py"],
                "required": [{"file": "tests/test_a.py"}],
                "advisory": [],
                "forced_failed": [],
                "reasons": {},
                "empty": False,
            }
            req = VerificationRequest(changed_files=["a.py"])
            vres = verify(
                req,
                project_root=str(root),
                receipt={"tx_id": 1, "version": 1},
                test_targets=targets,
            )
            self.assertEqual(vres.status, CheckStatus.FAIL)
            self.assertTrue(vres.evidence.get("selected_test_failure"))
            self.assertTrue(vres.evidence.get("test_results"))
            sf = (vres.evidence or {}).get("structured_failures") or []
            codes = {x["code"] for x in sf}
            self.assertIn(FailureClass.TEST_SELECTION_FAILURE.value, codes)


class TestClassifier(unittest.TestCase):
    def test_selection_failure_enum(self):
        vres = VerificationResult(
            status=CheckStatus.FAIL,
            receipt_ok=True,
            projection_ok=True,
            build_ok=False,
            failures=["build: tests failed"],
            evidence={
                "test_failure": True,
                "selected_test_failure": True,
                "failed_files": ["tests/test_a.py"],
                "build_checks": ["pytest"],
                "test_results": [
                    {"test_name": "t", "status": "failed", "file": "tests/test_a.py"}
                ],
            },
        )
        recs = classify_verification_result(vres)
        self.assertTrue(
            any(r.code == FailureClass.TEST_SELECTION_FAILURE.value for r in recs)
        )


if __name__ == "__main__":
    unittest.main()

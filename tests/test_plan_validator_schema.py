"""PlanValidator schema strictness — machine gate for LLM plan payloads."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from forge.plan_validator import PlanValidationError, PlanValidator
from forge.protocols.models import RepoContext


def _repo(td: str, rel: str, body: str) -> tuple[PlanValidator, RepoContext]:
    p = Path(td) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    repo = RepoContext(file_tree=[rel])
    return PlanValidator(td), repo


class TestModifyRequiresNewText(unittest.TestCase):
    def test_missing_new_text_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            v, repo = _repo(td, "a.py", "x = 1\ny = 2\n")
            plan = {
                "goal": "edit",
                "steps": [
                    {
                        "step_id": "step_1",
                        "description": "change",
                        "target_files": ["a.py"],
                        "operation_type": "modify",
                        "start_line": 1,
                        "end_line": 1,
                        # deliberately no new_text
                    }
                ],
            }
            with self.assertRaises(PlanValidationError) as cm:
                v.validate(plan, repo)
            self.assertIn("new_text", str(cm.exception))

    def test_null_new_text_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            v, repo = _repo(td, "a.py", "x = 1\n")
            plan = {
                "goal": "edit",
                "steps": [
                    {
                        "step_id": "step_1",
                        "description": "change",
                        "target_files": ["a.py"],
                        "operation_type": "modify",
                        "start_line": 1,
                        "end_line": 1,
                        "new_text": None,
                    }
                ],
            }
            with self.assertRaises(PlanValidationError) as cm:
                v.validate(plan, repo)
            self.assertIn("new_text", str(cm.exception))

    def test_empty_new_text_allowed(self):
        """Empty string means delete the line range."""
        with tempfile.TemporaryDirectory() as td:
            v, repo = _repo(td, "a.py", "x = 1\ny = 2\n")
            plan = {
                "goal": "delete line",
                "steps": [
                    {
                        "step_id": "step_1",
                        "description": "drop first line",
                        "target_files": ["a.py"],
                        "operation_type": "modify",
                        "start_line": 1,
                        "end_line": 1,
                        "new_text": "",
                    }
                ],
            }
            plan_obj, enriched = v.validate(plan, repo)
            self.assertEqual(plan_obj.steps[0].new_text, "")
            self.assertIn("old_text", enriched["steps"][0])
            self.assertEqual(enriched["steps"][0]["old_text"], "x = 1\n")

    def test_valid_new_text_fills_old_text(self):
        with tempfile.TemporaryDirectory() as td:
            v, repo = _repo(td, "a.py", "hello world\n")
            plan = {
                "goal": "exact delete substring",
                "steps": [
                    {
                        "step_id": "step_1",
                        "description": "remove ' world'",
                        "target_files": ["a.py"],
                        "operation_type": "modify",
                        "start_line": 1,
                        "end_line": 1,
                        "new_text": "hello\n",
                    }
                ],
            }
            plan_obj, _ = v.validate(plan, repo)
            self.assertEqual(plan_obj.steps[0].new_text, "hello\n")
            self.assertEqual(plan_obj.steps[0].old_text, "hello world\n")
            # content may be absent for modify — must not become 'MISSING'
            self.assertEqual(plan_obj.steps[0].content, "")


class TestCreateRequiresContent(unittest.TestCase):
    def test_missing_content_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            v = PlanValidator(td)
            repo = RepoContext(file_tree=[])
            plan = {
                "goal": "create",
                "steps": [
                    {
                        "step_id": "step_1",
                        "description": "new file",
                        "target_files": ["b.py"],
                        "operation_type": "create_file",
                    }
                ],
            }
            with self.assertRaises(PlanValidationError) as cm:
                v.validate(plan, repo)
            self.assertIn("content", str(cm.exception))

    def test_null_content_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            v = PlanValidator(td)
            repo = RepoContext(file_tree=[])
            plan = {
                "goal": "create",
                "steps": [
                    {
                        "step_id": "step_1",
                        "description": "new file",
                        "target_files": ["b.py"],
                        "operation_type": "create_file",
                        "content": None,
                    }
                ],
            }
            with self.assertRaises(PlanValidationError) as cm:
                v.validate(plan, repo)
            self.assertIn("content", str(cm.exception))


class TestNoMisleadingMissingDebug(unittest.TestCase):
    def test_modify_without_content_key_ok(self):
        """content key is optional for modify; validator must not invent MISSING."""
        with tempfile.TemporaryDirectory() as td:
            v, repo = _repo(td, "a.py", "z = 3\n")
            plan = {
                "goal": "edit",
                "steps": [
                    {
                        "step_id": "step_1",
                        "description": "change",
                        "target_files": ["a.py"],
                        "operation_type": "modify",
                        "start_line": 1,
                        "end_line": 1,
                        "new_text": "z = 4\n",
                    }
                ],
            }
            plan_obj, _ = v.validate(plan, repo)
            self.assertEqual(plan_obj.steps[0].content, "")
            self.assertEqual(plan_obj.steps[0].new_text, "z = 4\n")


if __name__ == "__main__":
    unittest.main()


class TestOperationTypeRequired(unittest.TestCase):
    """P1a: missing operation_type must REJECT — machine must not default to modify."""

    def test_missing_operation_type_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            v, repo = _repo(td, "a.py", "x = 1\n")
            plan = {
                "goal": "edit",
                "steps": [
                    {
                        "step_id": "step_1",
                        "description": "change",
                        "target_files": ["a.py"],
                        # deliberately no operation_type
                        "start_line": 1,
                        "end_line": 1,
                        "new_text": "y = 2\n",
                    }
                ],
            }
            with self.assertRaises(PlanValidationError) as cm:
                v.validate(plan, repo)
            msg = str(cm.exception)
            self.assertIn("operation_type", msg)
            # Must not silently accept as modify
            self.assertNotIn("old_text", getattr(cm.exception, "args", ()) or ())

    def test_null_operation_type_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            v, repo = _repo(td, "a.py", "x = 1\n")
            plan = {
                "goal": "edit",
                "steps": [
                    {
                        "step_id": "step_1",
                        "description": "change",
                        "target_files": ["a.py"],
                        "operation_type": None,
                        "start_line": 1,
                        "end_line": 1,
                        "new_text": "y = 2\n",
                    }
                ],
            }
            with self.assertRaises(PlanValidationError) as cm:
                v.validate(plan, repo)
            self.assertIn("operation_type", str(cm.exception))

    def test_empty_string_operation_type_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            v, repo = _repo(td, "a.py", "x = 1\n")
            plan = {
                "goal": "edit",
                "steps": [
                    {
                        "step_id": "step_1",
                        "description": "change",
                        "target_files": ["a.py"],
                        "operation_type": "",
                        "start_line": 1,
                        "end_line": 1,
                        "new_text": "y = 2\n",
                    }
                ],
            }
            with self.assertRaises(PlanValidationError) as cm:
                v.validate(plan, repo)
            self.assertIn("operation_type", str(cm.exception))

"""P0 Forge Edit Contract — golden unit + pipeline tests.

Proves:
  Authoring (1-based inclusive + new_text)
    → authoring_to_machine_ops (sole boundary)
    → Machine EditOp (0-based half-open + new_lines)
    → PatchEngine
    → exact filesystem bytes

Also covers Intent validation, projection-only machine schema,
and manufacturing status semantics.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock

from forge.core.edit_contract import (
    EditContractError,
    authoring_to_machine_op,
    authoring_to_machine_ops,
    ensure_machine_ops,
    text_to_new_lines,
    validate_machine_op,
)
from forge.core.patch_engine import PatchEngine


class TestTrailingNewline(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(text_to_new_lines(""), [])

    def test_with_final_newline(self):
        self.assertEqual(text_to_new_lines("a\nb\n"), ["a\n", "b\n"])

    def test_without_final_newline(self):
        self.assertEqual(text_to_new_lines("a\nb"), ["a\n", "b"])

    def test_single_line_with_nl(self):
        self.assertEqual(text_to_new_lines("x\n"), ["x\n"])

    def test_single_line_without_nl(self):
        self.assertEqual(text_to_new_lines("x"), ["x"])


class TestAuthoringToMachine(unittest.TestCase):
    def test_single_line_replace(self):
        # Authoring: line 3 inclusive → machine [2, 3)
        m = authoring_to_machine_op({
            "type": "replace",
            "start_line": 3,
            "end_line": 3,
            "new_text": "REPLACED\n",
        })
        self.assertEqual(m["start_line"], 2)
        self.assertEqual(m["end_line"], 3)
        self.assertEqual(m["new_lines"], ["REPLACED\n"])
        self.assertEqual(m["type"], "replace")

    def test_multi_line_replace(self):
        # Authoring [3, 5] inclusive → machine [2, 5)
        m = authoring_to_machine_op({
            "type": "replace",
            "start_line": 3,
            "end_line": 5,
            "new_text": "A\nB\n",
        })
        self.assertEqual(m["start_line"], 2)
        self.assertEqual(m["end_line"], 5)
        self.assertEqual(m["new_lines"], ["A\n", "B\n"])

    def test_delete(self):
        m = authoring_to_machine_op({
            "type": "delete",
            "start_line": 2,
            "end_line": 3,
            "new_text": "",
        })
        self.assertEqual(m["type"], "delete")
        self.assertEqual(m["start_line"], 1)
        self.assertEqual(m["end_line"], 3)
        self.assertEqual(m["new_lines"], [])

    def test_replace_empty_becomes_delete(self):
        m = authoring_to_machine_op({
            "type": "replace",
            "start_line": 1,
            "end_line": 1,
            "new_text": "",
        })
        self.assertEqual(m["type"], "delete")
        self.assertEqual(m["new_lines"], [])

    def test_insert(self):
        # Insert before line 3 → machine empty range [2, 2)
        m = authoring_to_machine_op({
            "type": "insert",
            "start_line": 3,
            "end_line": 3,
            "new_text": "INSERTED\n",
        })
        self.assertEqual(m["type"], "insert")
        self.assertEqual(m["start_line"], 2)
        self.assertEqual(m["end_line"], 2)
        self.assertEqual(m["new_lines"], ["INSERTED\n"])

    def test_insert_bad_range(self):
        with self.assertRaises(EditContractError):
            authoring_to_machine_op({
                "type": "insert",
                "start_line": 3,
                "end_line": 4,
                "new_text": "x\n",
            })

    def test_rejects_zero_based_authoring(self):
        with self.assertRaises(EditContractError):
            authoring_to_machine_op({
                "start_line": 0,
                "end_line": 1,
                "new_text": "x\n",
            })


class TestPatchEngineGolden(unittest.TestCase):
    """Authoring → machine → PatchEngine → exact bytes."""

    def _apply_authoring(self, original: str, authoring: dict) -> str:
        machine = authoring_to_machine_op(authoring)
        from forge.core.patch_engine import EditOp
        edit = EditOp(
            type=machine["type"],
            start_line=machine["start_line"],
            end_line=machine["end_line"],
            new_lines=machine["new_lines"],
        )
        return PatchEngine.apply_edits(original, [edit])

    def test_single_line_replace_bytes(self):
        original = "L1\nL2\nL3\nL4\n"
        out = self._apply_authoring(original, {
            "type": "replace",
            "start_line": 3,
            "end_line": 3,
            "new_text": "XXX\n",
        })
        self.assertEqual(out, "L1\nL2\nXXX\nL4\n")

    def test_multi_line_replace_bytes(self):
        original = "L1\nL2\nL3\nL4\nL5\n"
        out = self._apply_authoring(original, {
            "type": "replace",
            "start_line": 3,
            "end_line": 5,
            "new_text": "A\nB\n",
        })
        self.assertEqual(out, "L1\nL2\nA\nB\n")

    def test_delete_bytes(self):
        original = "L1\nL2\nL3\nL4\n"
        out = self._apply_authoring(original, {
            "type": "delete",
            "start_line": 2,
            "end_line": 3,
            "new_text": "",
        })
        self.assertEqual(out, "L1\nL4\n")

    def test_insert_bytes(self):
        original = "L1\nL2\nL3\n"
        out = self._apply_authoring(original, {
            "type": "insert",
            "start_line": 2,
            "end_line": 2,
            "new_text": "NEW\n",
        })
        self.assertEqual(out, "L1\nNEW\nL2\nL3\n")

    def test_trailing_newline_variants(self):
        original = "A\nB\n"
        with_nl = self._apply_authoring(original, {
            "type": "replace",
            "start_line": 1,
            "end_line": 1,
            "new_text": "X\n",
        })
        self.assertEqual(with_nl, "X\nB\n")
        without_nl = self._apply_authoring(original, {
            "type": "replace",
            "start_line": 1,
            "end_line": 1,
            "new_text": "X",
        })
        self.assertEqual(without_nl, "XB\n")


class TestEnsureMachineOps(unittest.TestCase):
    def test_passthrough_machine(self):
        ops = [{"type": "replace", "start_line": 1, "end_line": 2, "new_lines": ["x\n"]}]
        out = ensure_machine_ops(ops)
        self.assertEqual(out[0]["start_line"], 1)
        self.assertEqual(out[0]["new_lines"], ["x\n"])

    def test_convert_authoring(self):
        ops = [{"type": "replace", "start_line": 1, "end_line": 1, "new_text": "x\n"}]
        out = ensure_machine_ops(ops)
        self.assertEqual(out[0]["start_line"], 0)
        self.assertEqual(out[0]["end_line"], 1)

    def test_rejects_mixed(self):
        with self.assertRaises(EditContractError):
            ensure_machine_ops([
                {"start_line": 0, "end_line": 1, "new_lines": ["a"]},
                {"start_line": 1, "end_line": 1, "new_text": "b"},
            ])

    def test_machine_rejects_new_text(self):
        with self.assertRaises(EditContractError):
            validate_machine_op({
                "start_line": 0,
                "end_line": 1,
                "new_lines": ["a"],
                "new_text": "nope",
            })


class TestIntentExecutorMachineOnly(unittest.TestCase):
    def test_rejects_authoring_ops(self):
        from forge.intents.executor import IntentExecutor, IntentExecutionError
        from forge.intents.intent import Intent

        world = MagicMock()
        world.get_object.return_value = MagicMock(state="Alive")
        ex = IntentExecutor(world)
        intent = Intent.modify_file(
            path="/tmp/x.py",
            operations=[{
                "start_line": 1,
                "end_line": 1,
                "new_text": "x\n",
            }],
            require_confirm=False,
        )
        intent.parameters["object_id"] = 1
        with self.assertRaises(IntentExecutionError):
            ex._validate_intent(intent)

    def test_accepts_machine_ops(self):
        from forge.intents.executor import IntentExecutor
        from forge.intents.intent import Intent

        world = MagicMock()
        world.get_object.return_value = MagicMock(state="Alive")
        ex = IntentExecutor(world)
        intent = Intent.modify_file(
            path="/tmp/x.py",
            operations=[{
                "type": "replace",
                "start_line": 0,
                "end_line": 1,
                "new_lines": ["x\n"],
            }],
            require_confirm=False,
        )
        intent.parameters["object_id"] = 1
        ex._validate_intent(intent)  # must not raise


class TestFileProjectionMachineOnly(unittest.TestCase):
    def test_rejects_new_text(self):
        from forge.projections.file_projection import FileProjection
        fp = FileProjection(project_root=".")
        with self.assertRaises(ValueError):
            fp._dicts_to_edits([{
                "start_line": 0,
                "end_line": 1,
                "new_text": "x",
            }])

    def test_accepts_machine(self):
        from forge.projections.file_projection import FileProjection
        fp = FileProjection(project_root=".")
        edits = fp._dicts_to_edits([{
            "type": "replace",
            "start_line": 1,
            "end_line": 2,
            "new_lines": ['VERSION = "2.0"\n'],
        }])
        self.assertEqual(edits[0].start_line, 1)
        self.assertEqual(edits[0].end_line, 2)


class TestExecutionAdapterConversion(unittest.TestCase):
    """Conversion happens at ExecutionAdapter; Intent sees machine ops only."""

    def test_proposal_authoring_converted_before_intent(self):
        from forge.core.edit_contract import proposal_ops_to_machine
        op = {
            "type": "modify",
            "target_files": ["a.py"],
            "start_line": 2,
            "end_line": 2,
            "new_text": "hello\n",
            "old_text": "old\n",
        }
        machine = proposal_ops_to_machine(op)
        self.assertEqual(len(machine), 1)
        self.assertEqual(machine[0]["start_line"], 1)
        self.assertEqual(machine[0]["end_line"], 2)
        self.assertEqual(machine[0]["new_lines"], ["hello\n"])
        self.assertNotIn("new_text", machine[0])
        self.assertNotIn("old_text", machine[0])


class TestProjectionFailureStatus(unittest.TestCase):
    def test_status_constants_on_result_model(self):
        from forge.protocols.models import ExecutionResult
        r = ExecutionResult(success=False, status="WORLD_COMMITTED_PROJECTION_FAILED")
        self.assertEqual(r.status, "WORLD_COMMITTED_PROJECTION_FAILED")
        self.assertFalse(r.success)
        r2 = ExecutionResult.from_dict({
            "success": False,
            "receipt_summary": {"projection_failed": True},
        })
        self.assertEqual(r2.status, "WORLD_COMMITTED_PROJECTION_FAILED")


if __name__ == "__main__":
    unittest.main()

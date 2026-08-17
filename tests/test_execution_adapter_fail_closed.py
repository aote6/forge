"""P1b RED: ExecutionAdapter must be strictly fail-closed on Intent boundary.

These tests call ExecutionAdapter.execute_proposal directly — they intentionally
bypass Planner / PlanValidator so the Adapter's own boundary is under test.

Expected (after fix):
  - missing type / operation_type → REJECT (not default "modify")
  - unknown type → REJECT (not fallthrough to modify)
  - non-create_object + empty targets → REJECT (not silent continue)
  - conflicting type vs operation_type → REJECT (not pick one)

create_object + empty targets remains legal.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from forge.adapters.execution import ExecutionAdapter
from forge.protocols.models import ChangeProposal


def _adapter(project_root: str | None = None) -> ExecutionAdapter:
    world = MagicMock()
    world._path_map = {}
    world.abort_session = MagicMock()
    projections = MagicMock()
    projections.object_path_map = None
    root = project_root or tempfile.mkdtemp()
    return ExecutionAdapter(world, projections, root)


class TestMissingOpTypeFailClosed(unittest.TestCase):
    """RED-1: missing type / operation_type must not default to modify."""

    def test_missing_both_type_fields_rejects(self):
        ex = _adapter()
        proposal = ChangeProposal(
            proposal_id="red1",
            plan_id="pl",
            target_files=["a.py"],
            operations=[
                {
                    # neither "type" nor "operation_type"
                    "target_files": ["a.py"],
                    "new_text": "x = 1",
                    "old_text": "x = 0",
                    "start_line": 1,
                    "end_line": 1,
                }
            ],
        )
        result = ex.execute_proposal(proposal)
        self.assertFalse(result.success, "missing op type must fail")
        err = (result.error or "").lower()
        # Must not have silently entered the modify path.
        self.assertNotIn(
            "modify requires object_id",
            err,
            "must not default to modify and then fail on object_id",
        )
        self.assertTrue(
            any(
                token in err
                for token in (
                    "operation type",
                    "operation_type",
                    "missing",
                    "type",
                    "op_type",
                )
            ),
            f"error must clearly indicate missing operation type, got: {result.error!r}",
        )


class TestUnknownOpTypeFailClosed(unittest.TestCase):
    """RED-2: unknown operation type must not fallthrough to modify."""

    def test_unknown_type_rejects(self):
        ex = _adapter()
        proposal = ChangeProposal(
            proposal_id="red2",
            plan_id="pl",
            target_files=["a.py"],
            operations=[
                {
                    "type": "foobar",
                    "target_files": ["a.py"],
                    "new_text": "x = 1",
                    "old_text": "x = 0",
                    "start_line": 1,
                    "end_line": 1,
                }
            ],
        )
        result = ex.execute_proposal(proposal)
        self.assertFalse(result.success, "unknown op type must fail")
        err = (result.error or "").lower()
        self.assertNotIn(
            "modify requires object_id",
            err,
            "must not fallthrough to modify path",
        )
        self.assertTrue(
            any(
                token in err
                for token in ("unknown", "invalid", "foobar", "operation type", "operation_type")
            ),
            f"error must indicate unknown/invalid operation type, got: {result.error!r}",
        )


class TestEmptyTargetsFailClosed(unittest.TestCase):
    """RED-3: non-create_object + empty targets must not silent-continue."""

    def test_modify_with_empty_targets_rejects(self):
        ex = _adapter()
        proposal = ChangeProposal(
            proposal_id="red3a",
            plan_id="pl",
            target_files=[],
            operations=[
                {
                    "type": "modify",
                    "target_files": [],
                    "new_text": "x = 1",
                    "old_text": "x = 0",
                    "start_line": 1,
                    "end_line": 1,
                }
            ],
        )
        result = ex.execute_proposal(proposal)
        self.assertFalse(result.success, "empty targets for modify must fail")
        err = (result.error or "").lower()
        # Current code silent-continues then raises the generic
        # "proposal has no executable operations". That is NOT acceptable:
        # require an explicit empty-targets rejection at the op boundary.
        self.assertNotEqual(
            err.strip(),
            "proposal has no executable operations",
            "must not silent-continue then raise generic no-ops; "
            "reject empty targets explicitly",
        )
        self.assertTrue(
            any(token in err for token in ("target", "empty", "missing")),
            f"error must explicitly indicate empty/missing targets, got: {result.error!r}",
        )

    def test_partial_empty_targets_must_not_silent_drop(self):
        """Critical: one illegal op + one legal-looking op must not partial-execute.

        On current code the empty-targets op is continued away; the second op
        then hits modify→object_id failure. After fix the first illegal op must
        reject the whole proposal before any Intent is built for the second.
        """
        root = tempfile.mkdtemp()
        # Create a real file so path resolve does not fail for the second op.
        a_py = Path(root) / "a.py"
        a_py.write_text("x = 0\n", encoding="utf-8")

        world = MagicMock()
        # Provide object_id so second op would otherwise proceed into Intent path.
        world._path_map = {str(a_py): 42}
        world.abort_session = MagicMock()
        projections = MagicMock()
        projections.object_path_map = None
        ex = ExecutionAdapter(world, projections, root)

        proposal = ChangeProposal(
            proposal_id="red3b",
            plan_id="pl",
            target_files=["a.py"],
            operations=[
                {
                    "type": "modify",
                    "target_files": [],  # illegal for modify
                    "new_text": "bad",
                    "old_text": "old",
                    "start_line": 1,
                    "end_line": 1,
                },
                {
                    "type": "modify",
                    "target_files": ["a.py"],
                    "object_id": 42,
                    "new_text": "x = 1",
                    "old_text": "x = 0",
                    "start_line": 1,
                    "end_line": 1,
                },
            ],
        )
        result = ex.execute_proposal(proposal)
        self.assertFalse(
            result.success,
            "partial empty targets must fail the proposal; silent drop is forbidden",
        )
        # Must not have reached execute_batch (no successful Intent path).
        world.begin_session.assert_not_called()
        # Error should point at targets / empty, not only at a later modify issue.
        err = (result.error or "").lower()
        self.assertTrue(
            any(t in err for t in ("target", "empty", "missing")),
            f"error should cite empty targets, got: {result.error!r}",
        )


class TestConflictingTypeFieldsFailClosed(unittest.TestCase):
    """RED-4: type and operation_type disagree → reject, do not pick one."""

    def test_conflicting_type_and_operation_type_rejects(self):
        ex = _adapter()
        proposal = ChangeProposal(
            proposal_id="red4",
            plan_id="pl",
            target_files=["a.py"],
            operations=[
                {
                    "type": "modify",
                    "operation_type": "create_file",
                    "target_files": ["a.py"],
                    "content": "print(1)\n",
                    "new_text": "x = 1",
                    "old_text": "x = 0",
                    "start_line": 1,
                    "end_line": 1,
                }
            ],
        )
        result = ex.execute_proposal(proposal)
        self.assertFalse(result.success, "conflicting type fields must fail")
        err = (result.error or "").lower()
        self.assertTrue(
            any(
                token in err
                for token in ("conflict", "conflicting", "mismatch", "disagree")
            )
            or ("type" in err and "operation_type" in err),
            f"error must report type/operation_type conflict, got: {result.error!r}",
        )


class TestCreateObjectEmptyTargetsStillLegal(unittest.TestCase):
    """Sanity (not RED): create_object + [] remains a valid special Intent.

    This documents the exception to the empty-targets rule. It may pass or
    fail for unrelated reasons (world mock); we only assert it is NOT rejected
    solely for empty targets with a targets-related message when Intent path
    is reached. Kept soft so it does not block RED suite.
    """

    def test_create_object_empty_targets_not_rejected_for_targets(self):
        world = MagicMock()
        world._path_map = {}
        session = MagicMock()
        world.begin_session.return_value = session
        from forge.world.types import Receipt, TransactionDelta

        receipt = Receipt(tx_id=1, before_root=0, after_root=1, version=1)
        delta = TransactionDelta(objects_created=[1], memory_written=[])
        world.commit_session.return_value = (receipt, delta)
        projections = MagicMock()
        projections.project.return_value = []
        projections.object_path_map = None
        ex = ExecutionAdapter(world, projections, tempfile.mkdtemp())
        proposal = ChangeProposal(
            proposal_id="co1",
            plan_id="pl",
            target_files=[],
            operations=[{"type": "create_object", "target_files": []}],
        )
        result = ex.execute_proposal(proposal)
        # If it fails, it must not be because of empty targets policy.
        if not result.success:
            err = (result.error or "").lower()
            self.assertNotIn("empty target", err)
            self.assertNotIn("missing target", err)


if __name__ == "__main__":
    unittest.main()

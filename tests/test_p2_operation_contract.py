"""P2 — Boundary Contract Normalization tests.

PlanStep / ChangeProposal / checkpoint structure fail-closed.
Does not re-test full PlanValidator business rules.
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest.mock import MagicMock

from forge.adapters.execution import ExecutionAdapter
from forge.intents.executor import IntentExecutionError
from forge.memory.checkpoint import CheckpointStore
from forge.protocols.models import (
    ChangeProposal,
    Plan,
    PlanStep,
    TaskCheckpoint,
)
from forge.protocols.operation_contract import (
    CANONICAL_PLAN_OPERATION_TYPES,
    LEGACY_PLAN_OPERATION_ALIASES,
    OperationContractError,
    normalize_plan_op_type,
    validate_checkpoint_structure,
)


class TestSSOT(unittest.TestCase):
    def test_canonical_set(self):
        self.assertEqual(
            CANONICAL_PLAN_OPERATION_TYPES,
            frozenset({"modify", "create_file", "delete_file", "create_object"}),
        )

    def test_legacy_aliases(self):
        self.assertEqual(
            LEGACY_PLAN_OPERATION_ALIASES,
            {"create": "create_file", "delete": "delete_file"},
        )

    def test_normalize_rejects_alias_without_flag(self):
        with self.assertRaises(OperationContractError):
            normalize_plan_op_type("create", allow_legacy_alias=False)

    def test_normalize_accepts_alias_with_flag(self):
        self.assertEqual(
            normalize_plan_op_type("create", allow_legacy_alias=True),
            "create_file",
        )
        self.assertEqual(
            normalize_plan_op_type("delete", allow_legacy_alias=True),
            "delete_file",
        )


class TestPlanStepFromDict(unittest.TestCase):
    def _base(self, **over):
        d = {
            "step_id": "s1",
            "description": "d",
            "operation_type": "modify",
            "target_files": ["a.py"],
        }
        d.update(over)
        return d

    def test_missing_operation_type_rejected(self):
        d = self._base()
        del d["operation_type"]
        with self.assertRaises(OperationContractError) as cm:
            PlanStep.from_dict(d)
        self.assertIn("operation_type", str(cm.exception))

    def test_null_operation_type_rejected(self):
        with self.assertRaises(OperationContractError):
            PlanStep.from_dict(self._base(operation_type=None))

    def test_empty_operation_type_rejected(self):
        with self.assertRaises(OperationContractError):
            PlanStep.from_dict(self._base(operation_type=""))

    def test_unknown_operation_type_rejected(self):
        with self.assertRaises(OperationContractError) as cm:
            PlanStep.from_dict(self._base(operation_type="refactor"))
        self.assertIn("unknown", str(cm.exception).lower())

    def test_alias_create_rejected_on_plan_step(self):
        with self.assertRaises(OperationContractError):
            PlanStep.from_dict(self._base(operation_type="create"))

    def test_target_files_str_rejected(self):
        with self.assertRaises(OperationContractError) as cm:
            PlanStep.from_dict(self._base(target_files="a.py"))
        self.assertIn("list", str(cm.exception).lower())

    def test_target_files_null_when_key_present_rejected(self):
        with self.assertRaises(OperationContractError):
            PlanStep.from_dict(self._base(target_files=None))

    def test_legal_list_ok(self):
        step = PlanStep.from_dict(self._base())
        self.assertEqual(step.operation_type, "modify")
        self.assertEqual(step.target_files, ["a.py"])

    def test_create_object_empty_targets_ok(self):
        step = PlanStep.from_dict(
            {
                "step_id": "s1",
                "description": "birth",
                "operation_type": "create_object",
                "target_files": [],
            }
        )
        self.assertEqual(step.operation_type, "create_object")
        self.assertEqual(step.target_files, [])


class TestChangeProposalFromDict(unittest.TestCase):
    def test_target_files_str_rejected(self):
        with self.assertRaises(OperationContractError):
            ChangeProposal.from_dict(
                {
                    "proposal_id": "p1",
                    "target_files": "a.py",
                    "operations": [{"type": "modify", "target_files": ["a.py"]}],
                }
            )

    def test_op_missing_type_rejected(self):
        with self.assertRaises(OperationContractError):
            ChangeProposal.from_dict(
                {
                    "proposal_id": "p1",
                    "target_files": ["a.py"],
                    "operations": [{"target_files": ["a.py"]}],
                }
            )

    def test_op_target_files_str_rejected(self):
        with self.assertRaises(OperationContractError):
            ChangeProposal.from_dict(
                {
                    "proposal_id": "p1",
                    "target_files": ["a.py"],
                    "operations": [{"type": "modify", "target_files": "a.py"}],
                }
            )

    def test_legacy_alias_in_operations_accepted(self):
        # Resume may carry Adapter-era aliases; structure gate allows them.
        p = ChangeProposal.from_dict(
            {
                "proposal_id": "p1",
                "target_files": ["a.py"],
                "operations": [
                    {"type": "create", "target_files": ["a.py"], "content": "x\n"}
                ],
            }
        )
        self.assertEqual(p.operations[0]["type"], "create")


class TestAdapterTargetFilesType(unittest.TestCase):
    def setUp(self):
        self.world = MagicMock()
        self.projections = MagicMock()
        self.root = tempfile.mkdtemp()
        self.ex = ExecutionAdapter(self.world, self.projections, self.root)

    def test_target_files_str_rejected(self):
        proposal = ChangeProposal(
            proposal_id="p1",
            plan_id="pl",
            target_files=["a.py"],
            operations=[{"type": "modify", "target_files": "a.py"}],
        )
        result = self.ex.execute_proposal(proposal)
        self.assertFalse(result.success)
        self.assertIn("list", (result.error or "").lower())

    def test_create_alias_still_accepted(self):
        """Legacy alias remains Adapter-compatible (P2-5)."""
        # Will fail later on path/security or object resolution, but must not
        # reject for unknown operation type.
        proposal = ChangeProposal(
            proposal_id="p1",
            plan_id="pl",
            target_files=["ok.py"],
            operations=[
                {
                    "type": "create",
                    "target_files": ["ok.py"],
                    "content": "print(1)\n",
                }
            ],
        )
        # Mock commit path so we observe type resolution success
        session = MagicMock()
        self.world.begin_session.return_value = session
        from forge.world.types import Receipt, TransactionDelta

        receipt = Receipt(tx_id=1, before_root=0, after_root=1, version=1)
        delta = TransactionDelta(objects_created=[1], memory_written=[])
        self.world.commit_session.return_value = (receipt, delta)
        self.projections.project.return_value = []
        # IntentExecutor is real; create_file may need session APIs.
        # Accept either success or a non-"unknown operation" failure.
        result = self.ex.execute_proposal(proposal)
        err = (result.error or "").lower()
        self.assertNotIn("unknown operation type", err)
        self.assertNotIn("missing operation type", err)


class TestCheckpointStructureGate(unittest.TestCase):
    def test_validate_missing_op_on_plan_step(self):
        # Construct in-memory with empty op (constructor allows ""), gate rejects.
        cp = TaskCheckpoint(
            task_id="t1",
            phase="executing",
            plan=Plan(
                plan_id="p",
                goal="g",
                steps=[
                    PlanStep(
                        step_id="s1",
                        description="d",
                        target_files=["a.py"],
                        operation_type="",  # invalid
                    )
                ],
            ),
        )
        with self.assertRaises(OperationContractError):
            validate_checkpoint_structure(cp)

    def test_validate_str_target_files_on_proposal(self):
        cp = TaskCheckpoint(
            task_id="t1",
            phase="executing",
            change_proposals=[
                ChangeProposal(
                    proposal_id="p1",
                    target_files=["a.py"],
                    operations=[{"type": "modify", "target_files": "a.py"}],  # type: ignore
                )
            ],
        )
        # operations stored as constructed; gate must reject
        with self.assertRaises(OperationContractError):
            validate_checkpoint_structure(cp)

    def test_load_corrupt_plan_step_via_store(self):
        root = tempfile.mkdtemp()
        store = CheckpointStore(root)
        task_id = "task_corrupt_op"
        # Write raw JSON missing operation_type
        path = store._path(task_id)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        payload = {
            "task_id": task_id,
            "phase": "executing",
            "goal": "g",
            "plan": {
                "plan_id": "p",
                "goal": "g",
                "steps": [
                    {
                        "step_id": "s1",
                        "description": "d",
                        "target_files": ["a.py"],
                        # no operation_type
                    }
                ],
            },
            "change_proposals": [],
            "completed_steps": [],
            "errors": [],
            "extra": {},
        }
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f)
        with self.assertRaises(OperationContractError):
            store.load(task_id)


if __name__ == "__main__":
    unittest.main()

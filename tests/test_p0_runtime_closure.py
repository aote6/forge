"""P0 Runtime Closure tests — no second mutation path, no committed replay."""
from __future__ import annotations

import inspect
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock
from unittest.mock import patch as mock_patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from forge.adapters.execution import ExecutionAdapter
from forge.adapters.lu_patch_adapter import LuWriteForbidden
from forge.adapters.lu_patch_adapter import create as lu_create
from forge.adapters.lu_patch_adapter import delete as lu_delete
from forge.adapters.lu_patch_adapter import patch as lu_patch
from forge.adapters import hub_adapter
from forge.intents.executor import IntentExecutionError
from forge.orchestrator.engine import EngineeringOrchestrator
from forge.protocols.models import (
    ChangeProposal,
    CheckStatus,
    ExecutionResult,
    OrchestratorPhase,
    Plan,
    PlanStep,
    TaskCheckpoint,
    VerificationResult,
)
from forge.projections.file_projection import FileProjection
from forge.world.types import Receipt, TransactionDelta


class TestLuWriteDisabled(unittest.TestCase):
    def test_patch_raises(self):
        with self.assertRaises(LuWriteForbidden):
            lu_patch("/tmp/x.py", "a", "b")

    def test_create_raises(self):
        with self.assertRaises(LuWriteForbidden):
            lu_create("/tmp/x.py", "content")

    def test_delete_raises(self):
        with self.assertRaises(LuWriteForbidden):
            lu_delete("/tmp/x.py")


class TestHubNoDirectNode(unittest.TestCase):
    def test_call_node_removed(self):
        with self.assertRaises(RuntimeError) as cm:
            hub_adapter._call_node("lu", "patch", {})
        self.assertIn("HubClient", str(cm.exception))

    def test_lu_patch_write_removed(self):
        with self.assertRaises(RuntimeError):
            hub_adapter.lu_patch("a.py", "old", "new")

    def test_lu_create_write_removed(self):
        with self.assertRaises(RuntimeError):
            hub_adapter.lu_create("a.py", "x")


class TestModifyRequiresObjectId(unittest.TestCase):
    def test_modify_without_object_id_fails(self):
        world = MagicMock()
        world._path_map = None
        projections = MagicMock()
        projections.object_path_map = None
        ex = ExecutionAdapter(world, projections, tempfile.mkdtemp())
        proposal = ChangeProposal(
            proposal_id="p1",
            plan_id="pl",
            target_files=["a.py"],
            operations=[{
                "type": "modify",
                "target_files": ["a.py"],
                "new_text": "x=1",
                "old_text": "x=0",
            }],
        )
        result = ex.execute_proposal(proposal)
        self.assertFalse(result.success)
        self.assertIn("object_id", result.error or "")
        world.begin_session.assert_not_called()


class TestVerifyFailGoesToPlan(unittest.TestCase):
    def test_source_goes_to_planning(self):
        src = inspect.getsource(EngineeringOrchestrator._step)
        verify_idx = src.find("VERIFYING")
        section = src[verify_idx:]
        self.assertIn("OrchestratorPhase.PLANNING", section)
        # Must NOT clear completed_steps
        clear_completed = "self.checkpoint.completed_steps = []"
        # After VERIFYING section, clearing completed_steps is forbidden
        self.assertNotIn(clear_completed, section)
        self.assertIn("committed_receipts", section)

    def test_runtime_preserves_completed(self):
        root = tempfile.mkdtemp()
        world = MagicMock()
        projections = MagicMock()
        planner = MagicMock()
        hub = MagicMock()
        hub.invoke.return_value = MagicMock(ok=False, error="sms fail", data={})

        orch = EngineeringOrchestrator(
            project_root=root,
            world=world,
            projections=projections,
            planner=planner,
            hub=hub,
        )
        plan = Plan(
            plan_id="p",
            goal="g",
            steps=[PlanStep(step_id="s1", description="d", target_files=["a.py"], operation_type="modify",)],
        )
        orch.checkpoint = TaskCheckpoint(
            task_id="t",
            phase=OrchestratorPhase.VERIFYING.value,
            plan=plan,
            goal="g",
            completed_steps=["p_s1"],
            execution_results=[
                ExecutionResult(
                    proposal_id="p_s1",
                    success=True,
                    tx_id=7,
                    world_version=3,
                )
            ],
        )
        orch.phase = OrchestratorPhase.VERIFYING
        orch._correction_count = 0

        with mock_patch(
            "forge.orchestrator.engine.verification_verify",
            return_value=VerificationResult(
                status=CheckStatus.FAIL, failures=["build failed"]
            ),
        ):
            orch._step()

        self.assertEqual(orch.phase, OrchestratorPhase.PLANNING)
        self.assertEqual(orch.checkpoint.completed_steps, ["p_s1"])
        self.assertEqual(len(orch.checkpoint.execution_results), 1)
        self.assertEqual(
            orch.checkpoint.extra.get("committed_receipts")[0]["tx_id"], 7
        )


class TestDeleteProjectionUsesMetadata(unittest.TestCase):
    def test_delete_path_from_metadata(self):
        root = tempfile.mkdtemp()
        target = os.path.join(root, "doomed.py")
        with open(target, "w", encoding="utf-8") as f:
            f.write("bye\n")

        fp = FileProjection(project_root=root)
        delta = TransactionDelta(
            objects_deleted=[42],
            metadata={"deleted_paths": {42: target}},
        )
        receipt = Receipt(tx_id=1, before_root=0, after_root=1, version=1)
        result = fp.apply(receipt, delta)
        self.assertTrue(result.success)
        self.assertFalse(os.path.exists(target))


class TestProjectionFailureSurfaces(unittest.TestCase):
    def test_execution_fails_on_projection_failure(self):
        world = MagicMock()
        session = MagicMock()
        session.create_object.return_value = 1
        world.begin_session.return_value = session
        receipt = Receipt(tx_id=9, before_root=0, after_root=1, version=2)
        delta = TransactionDelta(objects_created=[1], memory_written=[])
        world.commit_session.return_value = (receipt, delta)

        projections = MagicMock()
        failed = MagicMock()
        failed.success = False
        failed.reason = "syntax boom"
        failed.name = "file"
        projections.project.return_value = [failed]

        root = tempfile.mkdtemp()
        ex = ExecutionAdapter(world, projections, root)
        proposal = ChangeProposal(
            proposal_id="c1",
            plan_id="p",
            target_files=["ok.py"],
            operations=[{
                "type": "create",
                "target_files": ["ok.py"],
                "content": "print(1)\n",
            }],
        )
        result = ex.execute_proposal(proposal)
        self.assertFalse(result.success)
        self.assertIn("projection_failed", result.error)
        self.assertEqual(result.tx_id, 9)
        self.assertTrue(result.receipt_summary.get("projection_failed"))


if __name__ == "__main__":
    unittest.main()

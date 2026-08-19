"""P1-A / P1-B regression: non-Orchestrator mutation blocked; confirm fails on projection failure."""
from __future__ import annotations

import inspect
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from forge.adapters.base import ToolResult
from forge.adapters.execution import ExecutionAdapter
from forge.intents.intent import Intent
from forge.protocols.models import ChangeProposal
from forge.tools import make_tools
from forge.tools.schemas import (
    MUTATION_TOOL_NAMES,
    READ_ONLY_TOOL_DECLARATIONS,
    TOOL_DECLARATIONS,
)
from forge.world.types import Receipt, TransactionDelta


class TestP1BConfirmProjectionSemantics(unittest.TestCase):
    def _make_confirm(self, project_results):
        ws = MagicMock()
        ws.project_root = tempfile.mkdtemp()
        world = MagicMock()
        session = MagicMock()
        session.closed = False
        world.current_session = session
        receipt = Receipt(
            tx_id=42,
            before_root=1,
            after_root=2,
            version=7,
            delta=TransactionDelta(),
        )
        world.commit_session.return_value = (receipt, receipt.delta)

        projections = MagicMock()
        projections.project.return_value = project_results

        tools, confirm_fn, abort_fn = make_tools(
            workspace=ws,
            world_runtime=world,
            projections=projections,
            allow_mutation=True,
        )
        self.assertIsNotNone(confirm_fn)
        return confirm_fn, world, receipt

    def test_confirm_ok_when_all_projections_succeed(self):
        ok = SimpleNamespace(name="file", success=True, reason="")
        confirm_fn, world, receipt = self._make_confirm([ok])
        result = confirm_fn()
        self.assertTrue(result.success)
        self.assertEqual(result.payload.get("tx_id"), 42)
        self.assertEqual(result.payload.get("version"), 7)
        self.assertFalse(result.payload.get("projection_failed"))
        world.commit_session.assert_called_once()

    def test_confirm_fail_when_projection_fails(self):
        bad = SimpleNamespace(name="file", success=False, reason="disk full")
        confirm_fn, world, receipt = self._make_confirm([bad])
        result = confirm_fn()
        self.assertFalse(result.success)
        self.assertIn("projection_failed", result.display)
        # Receipt evidence retained for recovery — no false success
        self.assertEqual(result.payload.get("tx_id"), 42)
        self.assertEqual(result.payload.get("version"), 7)
        self.assertTrue(result.payload.get("projection_failed"))
        # Commit already happened; we do not claim rollback
        world.commit_session.assert_called_once()
        world.abort_session.assert_not_called()

    def test_confirm_fail_mixed_projections(self):
        ok = SimpleNamespace(name="git", success=True, reason="")
        bad = SimpleNamespace(name="file", success=False, reason="syntax")
        confirm_fn, _, _ = self._make_confirm([ok, bad])
        result = confirm_fn()
        self.assertFalse(result.success)
        self.assertTrue(result.payload.get("projection_failed"))


class TestOrchestratorMutationStillWorks(unittest.TestCase):
    """P1-A must not break ExecutionAdapter → IntentExecutor path."""

    def test_execution_adapter_still_calls_execute_batch(self):
        world = MagicMock()
        projections = MagicMock()
        projections.project.return_value = [
            SimpleNamespace(name="file", success=True, reason="")
        ]
        receipt = Receipt(
            tx_id=1, before_root=0, after_root=1, version=1, delta=TransactionDelta()
        )
        world_map = MagicMock()
        world_map.find_object_id.return_value = None
        world._path_map = world_map

        adapter = ExecutionAdapter(world, projections, tempfile.mkdtemp())
        adapter.executor = MagicMock()
        adapter.executor.execute_batch.return_value = (receipt, receipt.delta)

        proposal = ChangeProposal(
            proposal_id="p1",
            plan_id="pl",
            target_files=["new_file.py"],
            operations=[{"type": "create_file", "target_files": ["new_file.py"], "content": "x"}],
            reason="test",
        )
        er = adapter.execute_proposal(proposal)
        self.assertTrue(er.success)
        adapter.executor.execute_batch.assert_called_once()
        projections.project.assert_called_once()

    def test_execution_adapter_projection_failure_still_fails(self):
        world = MagicMock()
        projections = MagicMock()
        projections.project.return_value = [
            SimpleNamespace(name="file", success=False, reason="boom")
        ]
        receipt = Receipt(
            tx_id=9, before_root=0, after_root=1, version=3, delta=TransactionDelta()
        )
        adapter = ExecutionAdapter(world, projections, tempfile.mkdtemp())
        adapter.executor = MagicMock()
        adapter.executor.execute_batch.return_value = (receipt, receipt.delta)

        proposal = ChangeProposal(
            proposal_id="p2",
            plan_id="pl",
            target_files=["a.py"],
            operations=[{"type": "create_file", "target_files": ["a.py"], "content": "y"}],
            reason="test",
        )
        er = adapter.execute_proposal(proposal)
        self.assertFalse(er.success)
        self.assertIn("projection_failed", er.error or "")
        self.assertTrue(er.receipt_summary.get("projection_failed"))


if __name__ == "__main__":
    unittest.main()

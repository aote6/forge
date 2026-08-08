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


class TestP1AReadOnlyToolSurface(unittest.TestCase):
    def test_default_declarations_exclude_mutation(self):
        names = {d["name"] for d in TOOL_DECLARATIONS}
        for m in MUTATION_TOOL_NAMES:
            self.assertNotIn(m, names)
        read_names = {d["name"] for d in READ_ONLY_TOOL_DECLARATIONS}
        self.assertEqual(names, read_names)

    def test_make_tools_default_no_mutation(self):
        ws = MagicMock()
        ws.project_root = tempfile.mkdtemp()
        tools, confirm_fn, abort_fn = make_tools(
            workspace=ws,
            world_runtime=MagicMock(),
            projections=MagicMock(),
        )
        for name in MUTATION_TOOL_NAMES:
            self.assertNotIn(name, tools)
        self.assertIsNone(confirm_fn)
        self.assertIsNone(abort_fn)
        # read tools present
        self.assertIn("list_files", tools)
        self.assertIn("read_file", tools)

    def test_make_tools_allow_mutation_registers_intent_tools(self):
        ws = MagicMock()
        ws.project_root = tempfile.mkdtemp()
        world = MagicMock()
        projections = MagicMock()
        tools, confirm_fn, abort_fn = make_tools(
            workspace=ws,
            world_runtime=world,
            projections=projections,
            allow_mutation=True,
        )
        for name in ("create_file", "modify_file", "delete_file", "link_objects"):
            self.assertIn(name, tools)
        self.assertIsNotNone(confirm_fn)
        self.assertIsNotNone(abort_fn)

    def test_runtime_tool_executor_rejects_mutation_names(self):
        from forge.runtime import ToolExecutor, Runtime

        # Defense in ToolExecutor even if a mutation name is forced in
        ex = ToolExecutor({"create_file": lambda **kw: ToolResult.ok(display="should not run")})
        tc = SimpleNamespace(name="create_file", arguments={"path": "x.py"})
        result = ex.execute(tc)
        self.assertFalse(result.success)
        self.assertIn("EngineeringOrchestrator", result.display)

        for name in MUTATION_TOOL_NAMES:
            tc = SimpleNamespace(name=name, arguments={})
            r = ex.execute(tc)
            self.assertFalse(r.success, msg=name)

    def test_runtime_init_uses_allow_mutation_false(self):
        src = inspect.getsource(Runtime.__init__) if False else None
        from forge import runtime as rt_mod

        src = inspect.getsource(rt_mod.Runtime.__init__)
        self.assertIn("allow_mutation=False", src)

    def test_conversation_and_legacy_use_read_only_declarations(self):
        from forge import runtime as rt_mod

        src_conv = inspect.getsource(rt_mod.Runtime._run_conversation)
        src_legacy = inspect.getsource(rt_mod.Runtime.run_legacy)
        self.assertIn("READ_ONLY_TOOL_DECLARATIONS", src_conv)
        self.assertIn("READ_ONLY_TOOL_DECLARATIONS", src_legacy)
        self.assertNotIn("MUTATION_TOOL_DECLARATIONS", src_conv)


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

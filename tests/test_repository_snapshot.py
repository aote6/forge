"""Priority 1: Versioned Repository Snapshot binding & execution guard."""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from forge.context.snapshot import (
    RepositorySnapshot,
    StaleSnapshotError,
    assert_snapshot_match,
    take_snapshot,
)
from forge.context import build_context
from forge.memory.checkpoint import CheckpointStore
from forge.orchestrator.engine import EngineeringOrchestrator
from forge.protocols.models import (
    ChangeProposal,
    OrchestratorPhase,
    Plan,
    PlanStep,
    TaskCheckpoint,
)
from forge.projections.base import ProjectionManager


def _write(repo: Path, rel: str, content: str) -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


class TestTreeHashStability(unittest.TestCase):
    def test_same_tree_same_hash(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(root, "a.py", "print(1)\n")
            _write(root, "pkg/b.py", "x = 2\n")
            s1 = take_snapshot(str(root))
            s2 = take_snapshot(str(root))
            self.assertEqual(s1.tree_hash, s2.tree_hash)
            self.assertEqual(s1.snapshot_id, s1.tree_hash)
            self.assertEqual(s1.snapshot_id, s2.snapshot_id)

    def test_changed_file_different_hash(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(root, "a.py", "print(1)\n")
            s1 = take_snapshot(str(root))
            _write(root, "a.py", "print(2)\n")
            s2 = take_snapshot(str(root))
            self.assertNotEqual(s1.tree_hash, s2.tree_hash)
            self.assertNotEqual(s1.snapshot_id, s2.snapshot_id)

    def test_new_file_different_hash(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(root, "a.py", "a\n")
            s1 = take_snapshot(str(root))
            _write(root, "b.py", "b\n")
            s2 = take_snapshot(str(root))
            self.assertNotEqual(s1.snapshot_id, s2.snapshot_id)

    def test_snapshot_id_equals_tree_hash(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(root, "x.py", "1\n")
            s = take_snapshot(str(root))
            self.assertEqual(s.snapshot_id, s.tree_hash)
            ctx = build_context(str(root), include_content=False)
            self.assertEqual(s.tree_hash, ctx.tree_hash)


class TestPlanSnapshotBinding(unittest.TestCase):
    def test_plan_fields_serialize(self):
        plan = Plan(
            plan_id="p1",
            goal="g",
            steps=[],
            snapshot_id="abc123",
            tree_hash="abc123",
            commit_hash="deadbeef",
        )
        d = plan.to_dict()
        self.assertEqual(d["snapshot_id"], "abc123")
        self.assertEqual(d["tree_hash"], "abc123")
        self.assertEqual(d["commit_hash"], "deadbeef")
        restored = Plan.from_dict(d)
        self.assertEqual(restored.snapshot_id, "abc123")
        self.assertEqual(restored.tree_hash, "abc123")
        self.assertEqual(restored.commit_hash, "deadbeef")


class TestCheckpointRoundTrip(unittest.TestCase):
    def test_checkpoint_snapshot_roundtrip(self):
        with tempfile.TemporaryDirectory() as td:
            store = CheckpointStore(td)
            plan = Plan(
                plan_id="pl",
                goal="goal",
                snapshot_id="snap_aaa",
                tree_hash="snap_aaa",
                commit_hash="c1",
            )
            cp = TaskCheckpoint(
                task_id="task_rt",
                phase=OrchestratorPhase.CHECKING.value,
                plan=plan,
                goal="goal",
                snapshot_id="snap_aaa",
                tree_hash="snap_aaa",
                commit_hash="c1",
            )
            store.save(cp)
            loaded = store.load("task_rt")
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.snapshot_id, "snap_aaa")
            self.assertEqual(loaded.tree_hash, "snap_aaa")
            self.assertEqual(loaded.commit_hash, "c1")
            self.assertIsNotNone(loaded.plan)
            self.assertEqual(loaded.plan.snapshot_id, "snap_aaa")
            self.assertEqual(loaded.plan.tree_hash, "snap_aaa")


class TestAssertSnapshotMatch(unittest.TestCase):
    def test_match_ok(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(root, "a.py", "ok\n")
            s = take_snapshot(str(root))
            cur = assert_snapshot_match(s.snapshot_id, str(root))
            self.assertEqual(cur.snapshot_id, s.snapshot_id)

    def test_stale_raises(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(root, "a.py", "v1\n")
            s = take_snapshot(str(root))
            _write(root, "a.py", "v2\n")
            with self.assertRaises(StaleSnapshotError) as cm:
                assert_snapshot_match(s.snapshot_id, str(root))
            self.assertEqual(cm.exception.code, "STALE_SNAPSHOT")
            self.assertEqual(cm.exception.planned_id, s.snapshot_id)
            self.assertNotEqual(cm.exception.current_id, s.snapshot_id)

    def test_empty_planned_id_raises(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(root, "a.py", "x\n")
            with self.assertRaises(StaleSnapshotError):
                assert_snapshot_match("", str(root))


class TestExecutionGuard(unittest.TestCase):
    """Stale plan must not call ExecutionAdapter / Veritas."""

    def _make_orch(self, root: str, plan: Plan):
        world = MagicMock()
        projections = ProjectionManager(checkpoint_dir=os.path.join(root, ".forge"))
        orch = EngineeringOrchestrator(
            project_root=root,
            world=world,
            projections=projections,
            planner=MagicMock(),
            checkpoint_store=CheckpointStore(root),
        )
        orch.execution = MagicMock()
        orch.execution.execute_proposal = MagicMock(
            return_value=SimpleNamespace(
                success=True,
                error="",
                tx_id=1,
                world_version=1,
                receipt_summary={},
            )
        )
        orch.checkpoint = TaskCheckpoint(
            task_id="t_guard",
            phase=OrchestratorPhase.EXECUTING.value,
            plan=plan,
            goal=plan.goal,
            snapshot_id=plan.snapshot_id,
            tree_hash=plan.tree_hash,
            change_proposals=[
                ChangeProposal(
                    proposal_id="p1",
                    plan_id=plan.plan_id,
                    target_files=["a.py"],
                    operations=[{"type": "create_file", "target_files": ["a.py"], "content": "z"}],
                    reason="test",
                )
            ],
        )
        orch.phase = OrchestratorPhase.EXECUTING
        return orch

    def test_matching_snapshot_allows_execution(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(root, "a.py", "v1\n")
            snap = take_snapshot(str(root))
            plan = Plan(
                plan_id="pl",
                goal="g",
                snapshot_id=snap.snapshot_id,
                tree_hash=snap.tree_hash,
                steps=[
                    PlanStep(
                        step_id="s1",
                        description="create",
                        target_files=["b.py"],
                        operation_type="create_file",
                        content="x",
                    )
                ],
            )
            orch = self._make_orch(str(root), plan)
            orch._step()
            orch.execution.execute_proposal.assert_called()
            self.assertNotEqual(orch.phase, OrchestratorPhase.FAILED)
            # After successful loop may be VERIFYING
            self.assertIn(
                orch.phase,
                (OrchestratorPhase.VERIFYING, OrchestratorPhase.EXECUTING, OrchestratorPhase.COMPLETED),
            )

    def test_stale_snapshot_rejects_zero_mutation(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(root, "a.py", "v1\n")
            snap = take_snapshot(str(root))
            plan = Plan(
                plan_id="pl",
                goal="g",
                snapshot_id=snap.snapshot_id,
                tree_hash=snap.tree_hash,
                steps=[],
            )
            # Change repo after plan binding
            _write(root, "a.py", "v2-changed\n")
            orch = self._make_orch(str(root), plan)
            orch._step()
            orch.execution.execute_proposal.assert_not_called()
            self.assertEqual(orch.phase, OrchestratorPhase.FAILED)
            self.assertTrue(
                any("STALE_SNAPSHOT" in e for e in orch.checkpoint.errors)
            )
            self.assertIn("stale_snapshot", orch.checkpoint.extra)
            self.assertEqual(
                orch.checkpoint.extra["stale_snapshot"]["code"], "STALE_SNAPSHOT"
            )

    def test_missing_plan_snapshot_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(root, "a.py", "x\n")
            plan = Plan(plan_id="pl", goal="g", snapshot_id="", tree_hash="")
            orch = self._make_orch(str(root), plan)
            orch.checkpoint.snapshot_id = ""
            orch._step()
            orch.execution.execute_proposal.assert_not_called()
            self.assertEqual(orch.phase, OrchestratorPhase.FAILED)


class TestOrchestratorBindsSnapshotOnPlan(unittest.TestCase):
    def test_planning_binds_snapshot_to_plan(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(root, "m.py", "1\n")
            snap = take_snapshot(str(root))

            fake_plan = Plan(
                plan_id="from_llm",
                goal="do thing",
                steps=[
                    PlanStep(
                        step_id="s1",
                        description="noop",
                        target_files=["m.py"],
                        operation_type="modify",
                        start_line=1,
                        end_line=1,
                        new_text="2\n",
                    )
                ],
            )
            planner = MagicMock()
            planner.plan.return_value = (fake_plan, {})

            world = MagicMock()
            orch = EngineeringOrchestrator(
                project_root=str(root),
                world=world,
                projections=ProjectionManager(checkpoint_dir=os.path.join(str(root), ".forge")),
                planner=planner,
                checkpoint_store=CheckpointStore(str(root)),
            )
            orch.checkpoint = TaskCheckpoint(
                task_id="t_bind",
                phase=OrchestratorPhase.PLANNING.value,
                goal="do thing",
                repo_context=MagicMock(file_tree=["m.py"], changed_files=[]),
            )
            orch.phase = OrchestratorPhase.PLANNING
            orch._step()
            self.assertIsNotNone(orch.checkpoint.plan)
            self.assertEqual(orch.checkpoint.plan.snapshot_id, snap.snapshot_id)
            self.assertEqual(orch.checkpoint.snapshot_id, snap.snapshot_id)
            self.assertEqual(orch.phase, OrchestratorPhase.CHECKING)


if __name__ == "__main__":
    unittest.main()

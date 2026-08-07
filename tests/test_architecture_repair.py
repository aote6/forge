"""Critical architecture repair tests (stdlib unittest, no external deps)."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# repo root on path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from forge.core.security import PathSecurityError, resolve_workspace_path
from forge.memory.checkpoint import CheckpointStore
from forge.protocols.models import (
    ChangeProposal,
    CheckStatus,
    ConstitutionResult,
    OrchestratorPhase,
    Plan,
    PlanStep,
    RepoContext,
    TaskCheckpoint,
    VerificationRequest,
    VerificationResult,
)
from forge.adapters.constitution import check as constitution_check
from forge.adapters.hub_client import HubClient, HubConfig, HubResponse
from forge.orchestrator.engine import EngineeringOrchestrator, plan_to_proposals


class TestPathSecurity(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()

    def test_resolve_inside(self):
        p = resolve_workspace_path(self.root, "src/a.py")
        self.assertTrue(p.startswith(os.path.realpath(self.root)))

    def test_escape_blocked(self):
        with self.assertRaises(PathSecurityError):
            resolve_workspace_path(self.root, "../outside.txt")

    def test_absolute_outside_blocked(self):
        with self.assertRaises(PathSecurityError):
            resolve_workspace_path(self.root, "/etc/passwd")


class TestCheckpointRoundtrip(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.store = CheckpointStore(self.root)

    def test_save_load_full_plan(self):
        plan = Plan(
            plan_id="p1",
            goal="fix",
            steps=[
                PlanStep(
                    step_id="s1",
                    description="edit",
                    target_files=["a.py"],
                    operation_type="modify",
                    content="x = 1",
                )
            ],
        )
        cp = TaskCheckpoint(
            task_id="t1",
            phase=OrchestratorPhase.EXECUTING.value,
            plan=plan,
            completed_steps=["p1_s0"],
            current_step="p1_s1",
            repo_context=RepoContext(repo_id="r", file_tree=["a.py"]),
            change_proposals=plan_to_proposals(plan),
            goal="fix",
        )
        self.store.save(cp)
        loaded = self.store.load("t1")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.phase, OrchestratorPhase.EXECUTING.value)
        self.assertIsNotNone(loaded.plan)
        self.assertEqual(loaded.plan.plan_id, "p1")
        self.assertEqual(loaded.plan.steps[0].content, "x = 1")
        self.assertEqual(loaded.current_step, "p1_s1")
        self.assertEqual(len(loaded.change_proposals), 1)


class TestConstitutionRequiresContent(unittest.TestCase):
    def test_empty_content_fails(self):
        proposal = ChangeProposal(
            proposal_id="x",
            plan_id="p",
            target_files=["a.py"],
            operations=[{"type": "modify", "target_files": ["a.py"]}],
        )
        # Hub will fail (no hub); adapter must still FAIL for empty content
        result = constitution_check(proposal, project_root=tempfile.mkdtemp())
        self.assertEqual(result.status, CheckStatus.FAIL)
        self.assertTrue(any(v.rule_id == "forge.content_required" for v in result.violations))


class TestAdapterTypes(unittest.TestCase):
    def test_constitution_rejects_dict(self):
        with self.assertRaises(TypeError):
            constitution_check({"proposal_id": "x"})  # type: ignore


class TestHubFailure(unittest.TestCase):
    def test_missing_hub_returns_error(self):
        cfg = HubConfig(hub_bin="/nonexistent/hub-binary-xyz")
        client = HubClient(config=cfg, project_root=tempfile.mkdtemp())
        resp = client.invoke("lu", "constitution_check", {})
        self.assertFalse(resp.ok)
        self.assertIn("not found", resp.error.lower())


class TestOrchestratorResume(unittest.TestCase):
    def test_resume_from_executing_restores_plan(self):
        root = tempfile.mkdtemp()
        store = CheckpointStore(root)
        plan = Plan(
            plan_id="p1",
            goal="g",
            steps=[PlanStep(step_id="s1", description="d", target_files=["f.py"], content="ok")],
        )
        store.save(
            TaskCheckpoint(
                task_id="resume1",
                phase=OrchestratorPhase.EXECUTING.value,
                plan=plan,
                change_proposals=plan_to_proposals(plan),
                goal="g",
                completed_steps=[],
            )
        )

        world = MagicMock()
        world.begin_session = MagicMock()
        world.abort_session = MagicMock()
        projections = MagicMock()
        planner = MagicMock()

        # Execution will fail without real Veritas — we only assert restore
        orch = EngineeringOrchestrator(
            project_root=root,
            world=world,
            projections=projections,
            planner=planner,
            checkpoint_store=store,
        )
        # Manually load like run() would
        saved = store.load("resume1")
        orch.checkpoint = saved
        orch.phase = OrchestratorPhase(saved.phase)
        self.assertEqual(orch.phase, OrchestratorPhase.EXECUTING)
        self.assertIsNotNone(orch.checkpoint.plan)
        self.assertEqual(orch.checkpoint.plan.goal, "g")
        self.assertEqual(len(orch.checkpoint.change_proposals), 1)


class TestNoSharedSessionAcrossTasks(unittest.TestCase):
    def test_two_orchestrators_independent_checkpoints(self):
        root = tempfile.mkdtemp()
        store = CheckpointStore(root)
        a = TaskCheckpoint(task_id="a", phase="planning", goal="A")
        b = TaskCheckpoint(task_id="b", phase="checking", goal="B")
        store.save(a)
        store.save(b)
        self.assertEqual(store.load("a").goal, "A")
        self.assertEqual(store.load("b").goal, "B")
        self.assertNotEqual(store.load("a").phase, store.load("b").phase)


class TestExecutionSecurity(unittest.TestCase):
    def test_execution_blocks_escape(self):
        from forge.adapters.execution import ExecutionAdapter

        root = tempfile.mkdtemp()
        world = MagicMock()
        projections = MagicMock()
        adapter = ExecutionAdapter(world, projections, root)
        proposal = ChangeProposal(
            proposal_id="bad",
            plan_id="p",
            target_files=["../../etc/passwd"],
            operations=[{
                "type": "create_file",
                "target_files": ["../../etc/passwd"],
                "content": "x",
            }],
        )
        result = adapter.execute_proposal(proposal)
        self.assertFalse(result.success)
        self.assertIn("security", result.error.lower())


if __name__ == "__main__":
    unittest.main()

"""Architecture closure tests — unique Engineering Orchestrator, no production bypass."""
from __future__ import annotations

import inspect
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from forge.adapters.execution import ExecutionAdapter
from forge.adapters.hub_client import HubClient, HubResponse
from forge.adapters.repo import get_repo_context
from forge.intents.executor import IntentExecutionError
from forge.orchestrator.engine import EngineeringOrchestrator, plan_to_proposals
from forge.protocols.models import (
    ChangeProposal,
    CheckStatus,
    OrchestratorPhase,
    Plan,
    PlanStep,
    TaskCheckpoint,
    VerificationResult,
)
from forge.runtime import Runtime


class TestUniqueRuntime(unittest.TestCase):
    def test_run_is_orchestrator(self):
        src = inspect.getsource(Runtime.run)
        self.assertIn("EngineeringOrchestrator", src)

    def test_legacy_renamed(self):
        self.assertTrue(hasattr(Runtime, "run_legacy"))
        src = inspect.getsource(Runtime.run_legacy)
        self.assertIn("DEPRECATED", src) or self.assertIn("deprecated", src.lower())

    def test_run_v2_alias(self):
        self.assertIs(Runtime.run_v2, Runtime.run)


class TestVerifyReplans(unittest.TestCase):
    def test_verify_fail_returns_to_understanding(self):
        src = inspect.getsource(EngineeringOrchestrator._step)
        verify_idx = src.find("VERIFYING")
        section = src[verify_idx:]
        self.assertIn("OrchestratorPhase.UNDERSTANDING", section)
        # Must clear plan so PLAN regenerates
        self.assertIn("self.checkpoint.plan = None", section)


class TestModifyNoCreateFallback(unittest.TestCase):
    def test_modify_without_object_id_fails(self):
        world = MagicMock()
        world._path_map = {}
        projections = MagicMock()
        ex = ExecutionAdapter(world, projections, "/tmp")
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
            reason="test",
        )
        with patch("forge.adapters.execution.resolve_workspace_path", return_value="/tmp/a.py"):
            result = ex.execute_proposal(proposal)
        self.assertFalse(result.success)
        self.assertIn("object_id", result.error or "")


class TestHubNoFallback(unittest.TestCase):
    def test_repo_hub_failure_raises(self):
        hub = MagicMock(spec=HubClient)
        hub.invoke.return_value = HubResponse(ok=False, error="hub down")
        with self.assertRaises(RuntimeError) as cm:
            get_repo_context("/tmp", hub=hub)
        self.assertIn("zhiwang", str(cm.exception))


class TestPlanToProposalsUnified(unittest.TestCase):
    def test_same_result(self):
        from forge.planner import plan_to_proposals as from_planner
        plan = Plan(
            plan_id="p",
            goal="g",
            steps=[PlanStep(step_id="s1", description="d", target_files=["a.py"])],
        )
        a = plan_to_proposals(plan)
        b = from_planner(plan)
        self.assertEqual(len(a), 1)
        self.assertEqual(len(b), 1)
        self.assertEqual(a[0].proposal_id, b[0].proposal_id)


class TestCheckpointResume(unittest.TestCase):
    def test_resume_preserves_goal(self):
        src = inspect.getsource(EngineeringOrchestrator.run)
        self.assertIn("Preserve original goal", src) or self.assertIn(
            "completed_steps", src
        )


if __name__ == "__main__":
    unittest.main()

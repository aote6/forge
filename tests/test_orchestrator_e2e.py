"""EngineeringOrchestrator structural E2E (pytest-native)."""
from __future__ import annotations

import shutil
import tempfile
from unittest.mock import MagicMock

from forge.memory.checkpoint import CheckpointStore
from forge.orchestrator.engine import EngineeringOrchestrator, plan_to_proposals
from forge.orchestrator.phases import OrchestratorPhase
from forge.protocols.models import Plan, PlanStep, TaskCheckpoint


def test_orchestrator_checkpoint_completed():
    root = tempfile.mkdtemp(prefix="forge_orch_e2e_")
    try:
        store = CheckpointStore(root)
        plan = Plan(
            plan_id="orch_001",
            goal="create orch_e2e_test.txt",
            steps=[
                PlanStep(
                    step_id="s1",
                    description="create",
                    target_files=["orch_e2e_test.txt"],
                    operation_type="create_file",
                    content="Orchestrator E2E test\n",
                )
            ],
        )
        proposals = plan_to_proposals(plan)
        store.save(
            TaskCheckpoint(
                task_id="orch_e2e_001",
                phase=OrchestratorPhase.COMPLETED.value,
                plan=plan,
                change_proposals=proposals,
                completed_steps=["s1"],
                goal=plan.goal,
            )
        )
        world = MagicMock()
        projections = MagicMock()
        planner = MagicMock()
        orch = EngineeringOrchestrator(
            project_root=root,
            world=world,
            projections=projections,
            planner=planner,
            checkpoint_store=store,
        )
        assert orch is not None
        loaded = store.load("orch_e2e_001")
        assert loaded is not None
        assert loaded.phase == "completed"
        assert loaded.plan is not None
        assert "Orchestrator E2E test" in (loaded.plan.steps[0].content or "")
        store.delete("orch_e2e_001")
    finally:
        shutil.rmtree(root, ignore_errors=True)

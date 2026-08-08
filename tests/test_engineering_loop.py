"""EngineeringOrchestrator E2E (EngineeringLoop.run is disabled).

Covers construction, deprecation of EngineeringLoop, and checkpoint
round-trip under the formal orchestrator phase model.
"""
from __future__ import annotations

import shutil
import tempfile
import warnings
from unittest.mock import MagicMock

import pytest

from forge.memory.checkpoint import CheckpointStore
from forge.orchestrator.engine import EngineeringOrchestrator, plan_to_proposals
from forge.orchestrator.phases import OrchestratorPhase
from forge.protocols.models import Plan, PlanStep, TaskCheckpoint


def test_engineering_loop_run_disabled():
    from forge.engineering import EngineeringLoop

    with warnings.catch_warnings(record=True) as w:
        warnings.simplefilter("always")
        loop = EngineeringLoop(".")
        assert any("deprecated" in str(x.message).lower() for x in w)

    with pytest.raises(RuntimeError, match="EngineeringLoop.run is disabled"):
        loop.run("any task", task_id="eng_loop_001")


def test_engineering_orchestrator_constructs_and_checkpoints():
    root = tempfile.mkdtemp(prefix="forge_orch_")
    try:
        store = CheckpointStore(root)
        plan = Plan(
            plan_id="eng_001",
            goal="create eng_loop_test.txt",
            steps=[
                PlanStep(
                    step_id="s1",
                    description="create file",
                    target_files=["eng_loop_test.txt"],
                    operation_type="create_file",
                    content="Engineering Loop E2E test\n",
                )
            ],
        )
        proposals = plan_to_proposals(plan)
        store.save(
            TaskCheckpoint(
                task_id="eng_loop_001",
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

        loaded = store.load("eng_loop_001")
        assert loaded is not None
        assert loaded.phase == OrchestratorPhase.COMPLETED.value
        assert loaded.plan is not None
        assert len(loaded.plan.steps) == 1
        assert len(loaded.change_proposals) == 1
        store.delete("eng_loop_001")
    finally:
        shutil.rmtree(root, ignore_errors=True)

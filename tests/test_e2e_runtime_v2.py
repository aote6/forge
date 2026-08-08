"""Forge v2 Runtime / Orchestrator contract E2E.

Validates public contracts only:
- Runtime constructs
- CheckpointStore phase uses OrchestratorPhase.COMPLETED ("completed")
- Plan lives on TaskCheckpoint / Orchestrator, not Runtime._plan
"""
from __future__ import annotations

import os
import shutil
import tempfile
from unittest.mock import MagicMock

from forge.memory.checkpoint import CheckpointStore
from forge.protocols.models import OrchestratorPhase, Plan, PlanStep, TaskCheckpoint
from forge.task_memory import make_checkpoint


def test_runtime_constructs():
    from forge.runtime import Runtime
    from forge.workspace import Workspace
    from forge.memory import MemoryStore

    root = tempfile.mkdtemp(prefix="forge_rt_")
    try:
        adapter = MagicMock()
        workspace = Workspace(project_root=root)
        memory = MemoryStore() if callable(MemoryStore) else MagicMock()
        try:
            runtime = Runtime(adapter, workspace, memory)
        except TypeError:
            # MemoryStore may need args in some revisions
            memory = MagicMock()
            runtime = Runtime(adapter, workspace, memory)
        assert runtime is not None
        assert not hasattr(runtime, "_plan") or runtime.__dict__.get("_plan") is None
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_checkpoint_phase_completed_not_done():
    """Formal terminal success phase is 'completed', not legacy 'done'."""
    root = tempfile.mkdtemp(prefix="forge_rt_cp_")
    try:
        store = CheckpointStore(root)
        plan = Plan(
            plan_id="p_rt",
            goal="runtime e2e",
            steps=[
                PlanStep(
                    step_id="s1",
                    description="noop",
                    target_files=["runtime_e2e_test.txt"],
                    operation_type="create_file",
                    content="Runtime v2 E2E test\n",
                )
            ],
        )
        cp = make_checkpoint(
            "e2e_runtime_test_001",
            OrchestratorPhase.COMPLETED.value,
            plan=plan,
            completed_steps=["s1"],
        )
        store.save(cp)
        loaded = store.load("e2e_runtime_test_001")
        assert loaded is not None
        assert loaded.phase == "completed"
        assert loaded.phase != "done"
        assert loaded.plan is not None
        assert len(loaded.plan.steps) >= 1
        tasks = store.list_tasks()
        assert any(t.get("task_id") == "e2e_runtime_test_001" for t in tasks)
        store.delete("e2e_runtime_test_001")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_orchestrator_terminal_phase_value():
    from forge.orchestrator.phases import OrchestratorPhase as OP

    assert OP.COMPLETED.value == "completed"
    assert "done" not in {p.value for p in OP}

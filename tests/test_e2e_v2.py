"""Forge v2 protocol smoke tests (pytest-native, no custom test helper)."""
from __future__ import annotations

from forge.protocols.models import (
    OrchestratorPhase,
    Plan,
    PlanStep,
    RepoContext,
    TaskCheckpoint,
)


def fake_planner(task: str, repo: RepoContext) -> Plan:
    return Plan(
        plan_id="fake_plan_001",
        goal=task,
        steps=[
            PlanStep(
                step_id="s1",
                description="modify readme",
                target_files=["README.md"],
                operation_type="modify",
                old_text="old",
                new_text="new",
            )
        ],
    )


def test_fake_plan_structure():
    ctx = RepoContext(file_tree=["README.md"], commit_hash="deadbeef")
    plan = fake_planner("修改 README", ctx)
    assert plan.plan_id == "fake_plan_001"
    assert len(plan.steps) == 1
    assert plan.steps[0].operation_type == "modify"


def test_task_checkpoint_formal_fields():
    cp = TaskCheckpoint(
        task_id="t1",
        phase=OrchestratorPhase.CHECKING.value
        if hasattr(OrchestratorPhase, "CHECKING")
        else "checking",
        completed_steps=["s1"],
        extra={"note": "v2"},
    )
    assert cp.task_id == "t1"
    assert cp.completed_steps == ["s1"]
    assert cp.extra["note"] == "v2"
    assert not hasattr(cp, "state") or not isinstance(getattr(cp, "state", None), dict)

"""Backward-compatible TaskMemory facade over CheckpointStore."""
from __future__ import annotations

from typing import Optional

from forge.memory.checkpoint import CheckpointStore
from forge.protocols.models import Plan, TaskCheckpoint


class TaskMemory(CheckpointStore):
    pass


def make_checkpoint(
    task_id: str,
    phase: str,
    plan: Optional[Plan] = None,
    completed_steps: Optional[list] = None,
    extra_state: Optional[dict] = None,
) -> TaskCheckpoint:
    return TaskCheckpoint(
        task_id=task_id,
        phase=phase,
        plan=plan,
        completed_steps=completed_steps or [],
        goal=(plan.goal if plan else ""),
        extra=dict(extra_state or {}),
    )

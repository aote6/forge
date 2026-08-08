"""Forge v2 full pipeline structural E2E (no custom test() helper)."""
from __future__ import annotations

import os
import shutil
import tempfile

from forge.protocols.models import (
    ChangeProposal,
    CheckStatus,
    Plan,
    PlanStep,
    RepoContext,
    VerificationRequest,
)


def test_full_pipeline_structural():
    plan = Plan(
        plan_id="full_001",
        goal="create hello.txt",
        steps=[
            PlanStep(
                step_id="s1",
                description="create",
                target_files=["hello.txt"],
                operation_type="create_file",
                content="hello\n",
            )
        ],
    )
    assert len(plan.steps) == 1

    proposal = ChangeProposal(
        proposal_id="full_001_s1",
        plan_id=plan.plan_id,
        target_files=["hello.txt"],
        operations=[{"type": "create_file", "content": "hello\n", "target_files": ["hello.txt"]}],
        reason=plan.goal,
    )
    assert proposal.proposal_id

    # Verification request shape
    vreq = VerificationRequest(changed_files=["hello.txt"], change_type="create_file")
    assert vreq.changed_files == ["hello.txt"]

    # Checkpoint formal phase
    from forge.memory.checkpoint import CheckpointStore
    from forge.protocols.models import OrchestratorPhase, TaskCheckpoint

    root = tempfile.mkdtemp(prefix="forge_full_")
    try:
        store = CheckpointStore(root)
        cp = TaskCheckpoint(
            task_id="full_pipeline_001",
            phase=OrchestratorPhase.COMPLETED.value,
            plan=plan,
            change_proposals=[proposal],
            completed_steps=["s1"],
            goal=plan.goal,
        )
        store.save(cp)
        loaded = store.load("full_pipeline_001")
        assert loaded is not None
        assert loaded.phase == "completed"
        store.delete("full_pipeline_001")
    finally:
        shutil.rmtree(root, ignore_errors=True)

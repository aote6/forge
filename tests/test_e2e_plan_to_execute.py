"""Forge v2: Planner.plan returns (Plan, enriched) → proposals → formal path."""
from __future__ import annotations

import shutil
import tempfile
from unittest.mock import MagicMock

from forge.planner import Planner, plan_to_proposals
from forge.protocols.models import Plan, PlanStep, RepoContext


def test_plan_return_contract_is_tuple():
    """Official API: Planner.plan(...) -> tuple[Plan, dict]."""
    raw = (
        '{"goal":"g","assumptions":[],"impact_files":["out_unique_xyz.txt"],'
        '"impact_symbols":[],'
        '"steps":[{"step_id":"s1","description":"create",'
        '"target_files":["out_unique_xyz.txt"],"operation_type":"create_file",'
        '"dependencies":[],"content":"hello\\n"}]}'
    )
    adapter = MagicMock()
    resp = MagicMock()
    resp.content = raw
    adapter.send.return_value = resp

    planner = Planner(adapter)
    repo = RepoContext(file_tree=["other.txt"], commit_hash="abc")
    with tempfile.TemporaryDirectory() as root:
        result = planner.plan(
            "create out_unique_xyz.txt with hello", repo, project_root=root
        )
    assert isinstance(result, tuple), f"expected tuple, got {type(result)}"
    assert len(result) == 2
    plan, enriched = result
    assert isinstance(plan, Plan)
    assert isinstance(enriched, dict)
    assert hasattr(plan, "steps")


def test_plan_to_proposals_from_plan_object():
    plan = Plan(
        plan_id="p1",
        goal="create file",
        steps=[
            PlanStep(
                step_id="s1",
                description="create",
                target_files=["e2e_plan_test.txt"],
                operation_type="create_file",
                content="Plan to Execute test\n",
            )
        ],
    )
    proposals = plan_to_proposals(plan)
    assert len(proposals) == 1
    assert proposals[0].target_files == ["e2e_plan_test.txt"]
    assert proposals[0].proposal_id


def test_plan_to_execute_structural():
    from forge.memory.checkpoint import CheckpointStore
    from forge.protocols.models import OrchestratorPhase, TaskCheckpoint

    root = tempfile.mkdtemp(prefix="forge_p2e_")
    try:
        plan = Plan(
            plan_id="e2e_plan_001",
            goal="在 tests/ 下创建一个 e2e_plan_test.txt 文件",
            steps=[
                PlanStep(
                    step_id="s1",
                    description="create e2e_plan_test.txt",
                    target_files=["e2e_plan_test.txt"],
                    operation_type="create_file",
                    content="Plan to Execute test\n",
                )
            ],
        )
        proposals = plan_to_proposals(plan)
        assert len(proposals) == len(plan.steps)

        store = CheckpointStore(root)
        cp = TaskCheckpoint(
            task_id="plan_to_execute_001",
            phase=OrchestratorPhase.COMPLETED.value,
            plan=plan,
            change_proposals=list(proposals),
            completed_steps=["s1"],
            goal=plan.goal,
        )
        store.save(cp)
        loaded = store.load("plan_to_execute_001")
        assert loaded is not None
        assert loaded.phase == OrchestratorPhase.COMPLETED.value
        assert loaded.plan is not None
        assert len(loaded.plan.steps) == 1
        store.delete("plan_to_execute_001")
    finally:
        shutil.rmtree(root, ignore_errors=True)

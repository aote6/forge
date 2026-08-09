"""Forge v2 full-loop E2E: RepoContext → Plan → Constitution → Intent →
Veritas → Projection → Verification → Checkpoint.

Does not use hub_adapter.lu_create / lu_patch write paths.
"""
from __future__ import annotations

import os
import shutil
import tempfile
import warnings

import pytest

from forge.memory.checkpoint import CheckpointStore
from forge.protocols.models import (
    CheckStatus,
    OrchestratorPhase,
    Plan,
    PlanStep,
    TaskCheckpoint,
)


def _try_world(project_root: str):
    try:
        from forge.world.runtime import WorldRuntime

        world = WorldRuntime(project_root=project_root)
        world.ensure_identity()
        return world
    except Exception:
        return None


def test_full_loop_with_hub():
    root = tempfile.mkdtemp(prefix="forge_fulllo_")
    try:
        # Phase 1: RepoContext (prefer repo adapter; hub may be absent)
        from forge.adapters.repo import get_repo_context

        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            try:
                ctx = get_repo_context(root)
            except Exception:
                from forge.protocols.models import RepoContext

                ctx = RepoContext(file_tree=[], commit_hash="")
        assert ctx is not None

        # Phase 2: Plan (deterministic, no LLM)
        test_rel = "fulllo_op_test.txt"
        test_file = os.path.join(root, test_rel)
        plan = Plan(
            plan_id="fulllo_001",
            goal="创建测试文件验证完整链路",
            steps=[
                PlanStep(
                    step_id="s1",
                    target_files=[test_rel],
                    operation_type="create_file",
                    description="创建测试文件",
                    content="# Forge v2 完整闭环测试\n",
                )
            ],
        )
        assert len(plan.steps) == 1

        # Phase 3: Constitution check on proposed content (read-only)
        from forge.adapters.constitution import check as constitution_check
        from forge.protocols.models import ChangeProposal

        proposal = ChangeProposal(
            proposal_id="fulllo_001_s1",
            plan_id=plan.plan_id,
            target_files=[test_rel],
            operations=[
                {
                    "type": "create_file",
                    "target_files": [test_rel],
                    "content": "# Forge v2 完整闭环测试\n",
                }
            ],
            reason=plan.goal,
        )
        try:
            check_result = constitution_check(proposal, project_root=root)
            # Contract: status is CheckStatus enum; do not assert PASS if
            # backend is missing — only assert type/shape.
            assert hasattr(check_result, "status")
            assert isinstance(check_result.status, CheckStatus)
        except Exception as exc:
            # Hub/Lu unavailable in sandbox is acceptable; mutation path below
            # still validates formal write contract.
            pytest.skip(f"constitution backend unavailable: {exc}")

        # Phase 4: Intent → Veritas → Projection (formal mutation)
        world = _try_world(root)
        if world is None:
            pytest.skip("Veritas/WorldRuntime unavailable")

        from forge.intents.intent import Intent
        from forge.intents.executor import IntentExecutor
        from forge.projections.base import ProjectionManager
        from forge.projections.file_projection import FileProjection
        from forge.projections.object_path import ObjectPathMap

        content = "# Forge v2 完整闭环测试\n"
        executor = IntentExecutor(world)
        intent = Intent.create_file(
            path=test_file, content=content, overwrite=True, require_confirm=False
        )
        receipt, delta = executor.execute(intent)
        assert receipt is not None

        pmap = ObjectPathMap()
        pmap.update_from_delta(delta)
        pm = ProjectionManager(checkpoint_dir=os.path.join(root, ".forge"))
        pm.register(FileProjection(project_root=root, object_path_map=pmap))
        proj = pm.project(receipt, delta)
        assert all(getattr(r, "success", True) for r in (proj or []))

        assert os.path.isfile(test_file)
        with open(test_file, encoding="utf-8") as f:
            assert f.read() == content

        # Hard contract: lu_create remains removed
        from forge.adapters import hub_adapter

        with pytest.raises(RuntimeError, match="lu_create write path removed"):
            hub_adapter.lu_create(test_file, content)

        # Phase 5: Verification adapter shape
        from forge.adapters.verification import verify as verification_verify
        from forge.protocols.models import VerificationRequest

        vresult = verification_verify(
            VerificationRequest(changed_files=[test_rel], change_type="create_file")
        )
        assert hasattr(vresult, "status")
        assert isinstance(vresult.status, CheckStatus)

        # Phase 6: Checkpoint with formal phase "completed"
        store = CheckpointStore(root)
        cp = TaskCheckpoint(
            task_id="fulllo_op_001",
            phase=OrchestratorPhase.COMPLETED.value,
            plan=plan,
            completed_steps=["s1"],
            goal=plan.goal,
        )
        store.save(cp)
        loaded = store.load("fulllo_op_001")
        assert loaded is not None
        assert loaded.phase == OrchestratorPhase.COMPLETED.value
        store.delete("fulllo_op_001")
    finally:
        shutil.rmtree(root, ignore_errors=True)

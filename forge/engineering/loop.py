"""Engineering Loop — 工程任务流程控制器

职责：从 UNDERSTAND 到 COMPLETE，驱动整个工程闭环。
不直接调用外部工具，通过 adapter 层完成所有操作。
"""
import sys
import os
from typing import Optional

from forge.engineering.phases import Phase
from forge.engineering.transitions import next_phase, MAX_RETRIES

from forge.protocols.models import (
    RepoContext, Plan, PlanStep, ChangeProposal,
    ConstitutionResult, VerificationRequest, VerificationResult,
    TransactionRequest, TaskCheckpoint, CheckStatus
)


class EngineeringLoop:
    """工程任务流程控制器"""

    def __init__(self, project_root: str):
        self.project_root = project_root
        self.phase = Phase.UNDERSTAND
        self.retry_count = 0
        self.task_id: str = ""
        self.goal: str = ""

        # 上下文累积
        self.repo_context: Optional[RepoContext] = None
        self.plan: Optional[Plan] = None
        self.proposals: list = []
        self.execution_results: list = []
        self.verification_result: Optional[VerificationResult] = None

    # ─── 主循环 ────────────────────────────────────────

    def run(self, task: str, task_id: str = None) -> str:
        """执行完整 Engineering Loop，返回结果报告"""
        self.task_id = task_id or f"task_{task[:20].replace(' ', '_')}"
        self.goal = task
        self.phase = Phase.UNDERSTAND
        self.retry_count = 0

        print(f"\n[Loop] 启动: {self.task_id} — {task}", file=sys.stderr)

        while self.phase not in (Phase.COMPLETE, Phase.FAILED):
            print(f"[Loop] Phase: {self.phase.value}", file=sys.stderr)

            success = self._execute_phase()

            old_phase = self.phase
            self.phase = next_phase(self.phase, success, self.retry_count)

            if not success and self.phase == old_phase:
                self.retry_count += 1
                print(f"[Loop] 重试 {self.retry_count}/{MAX_RETRIES}", file=sys.stderr)
            elif success:
                self.retry_count = 0

        if self.phase == Phase.COMPLETE:
            return f"✅ 任务完成: {self.goal}\n   步骤: {len(self.plan.steps) if self.plan else 0} 个"
        else:
            return f"❌ 任务失败: {self.goal}\n   最后阶段: {self.phase.value}"

    # ─── 阶段执行 ──────────────────────────────────────

    def _execute_phase(self) -> bool:
        """执行当前阶段，返回成功/失败"""
        if self.phase == Phase.UNDERSTAND:
            return self._do_understand()
        elif self.phase == Phase.PLAN:
            return self._do_plan()
        elif self.phase == Phase.REVIEW:
            return self._do_review()
        elif self.phase == Phase.EXECUTE:
            return self._do_execute()
        elif self.phase == Phase.VERIFY:
            return self._do_verify()
        return True

    def _do_understand(self) -> bool:
        """Phase 1: 仓库感知"""
        from forge.adapters.hub_adapter import get_repo_context
        self.repo_context = get_repo_context(self.project_root)
        return self.repo_context is not None and len(self.repo_context.file_tree) > 0

    def _do_plan(self) -> bool:
        """Phase 2: 生成计划"""
        from forge.planner import Planner
        from forge.adapters.deepseek import DeepSeekAdapter

        try:
            adapter = DeepSeekAdapter()
            planner = Planner(adapter)
            self.plan, _ = planner.plan(self.goal, self.repo_context, self.project_root)
            return self.plan is not None and len(self.plan.steps) > 0
        except Exception as e:
            print(f"[Loop] Planner 失败: {e}", file=sys.stderr)
            return False

    def _do_review(self) -> bool:
        """Phase 3: 宪法审查"""
        from forge.adapters.hub_adapter import check_constitution
        from forge.planner import plan_to_proposals

        self.proposals = plan_to_proposals(self.plan) if self.plan else []
        all_pass = True
        for prop in self.proposals:
            for target in prop.target_files:
                old = ""
                new = ""
                for op in prop.operations:
                    old = op.get("old_text", old) or old
                    new = op.get("new_text", new) or new
                result = check_constitution(target, old, new)
                if result.status == CheckStatus.FAIL:
                    all_pass = False
        return all_pass

    def _do_execute(self) -> bool:
        """Phase 4: 执行修改"""
        from forge.adapters.hub_adapter import lu_create, lu_patch, lu_delete
        from forge.adapters.veritas_adapter import VeritasAdapter

        va = VeritasAdapter(self.project_root)
        all_ok = True
        self.execution_results = []

        for prop in self.proposals:
            for op in prop.operations:
                op_type = op.get("type", "create_file")
                for target in prop.target_files:
                    full_path = os.path.join(self.project_root, target)

                    if op_type == "create_file":
                        ok = lu_create(full_path, op.get("content", ""))
                    elif op_type == "modify":
                        ok = lu_patch(full_path, op.get("old_text", ""), op.get("new_text", ""),
                                      op.get("start_line"), op.get("end_line"))
                    elif op_type == "delete_file":
                        ok = lu_delete(full_path)
                    else:
                        ok = False

                    if ok and os.path.exists(full_path):
                        with open(full_path) as f:
                            content = f.read()
                        receipt = va.execute(TransactionRequest(
                            request_id=f"{self.task_id}_{op.get('step_id', '?')}",
                            proposal_id=prop.proposal_id,
                            files=[{"path": full_path, "content": content, "operation": op_type}]
                        ))
                        self.execution_results.append({
                            "step": op.get("step_id", ""),
                            "file": target,
                            "tx_id": receipt.tx_id,
                            "version": receipt.version
                        })
                    elif not ok:
                        all_ok = False

        va.close()
        return all_ok

    def _do_verify(self) -> bool:
        """Phase 5: 验证"""
        from forge.adapters.hub_adapter import run_verification

        all_files = [r["file"] for r in self.execution_results]
        self.verification_result = run_verification(all_files)
        return self.verification_result.status == CheckStatus.PASS

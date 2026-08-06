"""Planner — 把 RepoContext + 用户任务 转化为 Plan"""
import json
import re
import sys
import time
from typing import Optional

from forge.contracts.repository import RepoContext
from forge.contracts.planning import Plan, PlanStep
from forge.adapters.base import BaseAdapter, Message

PLANNER_SYSTEM_PROMPT = """你是一个代码规划器。根据仓库文件列表和用户任务，生成一个有序的修改计划。

输出格式必须是严格的 JSON，不要包含任何其他文字：

{
  "goal": "用户任务的简洁重述",
  "assumptions": ["实现假设1", "假设2"],
  "steps": [
    {
      "step_id": "step_1",
      "description": "这一步做什么",
      "target_files": ["文件路径1"],
      "operation_type": "modify | create_file | delete_file",
      "dependencies": [],
      "content": "create_file 时的完整文件内容",
      "old_text": "modify 时被替换的原文",
      "new_text": "modify 时替换后的新文"
    }
  ]
}

规则：
1. modify/delete 的 target_files 必须来自仓库文件列表
2. create_file 的 target_files 是要新建的文件，可以不在列表中
3. modify 时必须提供 old_text 和 new_text，用于生成精确的替换
4. create_file 时必须提供 content，即完整的新文件内容
5. 只输出 JSON，不要输出任何解释文字
"""


class PlanValidationError(Exception):
    pass


class PlanValidator:
    """校验 LLM 输出的 Plan 结构是否合法"""

    def validate(self, plan_dict: dict, repo: RepoContext) -> Plan:
        if not isinstance(plan_dict, dict):
            raise PlanValidationError("Plan 必须是 dict")

        goal = plan_dict.get("goal", "")
        if not goal:
            raise PlanValidationError("Plan 缺少 goal")

        assumptions = plan_dict.get("assumptions", [])
        if not isinstance(assumptions, list):
            raise PlanValidationError("assumptions 必须是 list")

        steps_raw = plan_dict.get("steps", [])
        if not steps_raw:
            raise PlanValidationError("Plan 缺少 steps")
        if not isinstance(steps_raw, list):
            raise PlanValidationError("steps 必须是 list")

        existing_files = set(repo.file_tree)
        valid_ops = {"modify", "create_file", "delete_file"}
        step_ids = set()
        steps = []

        for i, s in enumerate(steps_raw):
            sid = s.get("step_id", f"step_{i+1}")
            if sid in step_ids:
                raise PlanValidationError(f"重复的 step_id: {sid}")
            step_ids.add(sid)

            desc = s.get("description", "")
            if not desc:
                raise PlanValidationError(f"{sid}: 缺少 description")

            targets = s.get("target_files", [])
            if not targets:
                raise PlanValidationError(f"{sid}: 缺少 target_files")

            op = s.get("operation_type", "modify")
            if op not in valid_ops:
                raise PlanValidationError(f"{sid}: 无效 operation_type '{op}'")

            for tf in targets:
                if op in ("modify", "delete_file"):
                    if tf not in existing_files:
                        raise PlanValidationError(f"{sid}: {op} 的目标 '{tf}' 不在仓库中")
                elif op == "create_file":
                    if tf in existing_files:
                        raise PlanValidationError(f"{sid}: create_file 的目标 '{tf}' 已存在")

            # 操作内容校验
            if op == "create_file":
                content = s.get("content", "")
                if not content:
                    raise PlanValidationError(f"{sid}: create_file 缺少 content")
            elif op == "modify":
                old_text = s.get("old_text", "")
                new_text = s.get("new_text", "")
                if not old_text:
                    raise PlanValidationError(f"{sid}: modify 缺少 old_text")

            deps = s.get("dependencies", [])
            if not isinstance(deps, list):
                raise PlanValidationError(f"{sid}: dependencies 必须是 list")

            steps.append(PlanStep(
                step_id=sid,
                description=desc,
                target_files=targets,
                operation_type=op,
                dependencies=deps
            ))

        for step in steps:
            for dep in step.dependencies:
                if dep not in step_ids:
                    raise PlanValidationError(f"{step.step_id}: 依赖的 {dep} 不存在")

        return Plan(
            plan_id=_make_plan_id(),
            goal=goal,
            steps=steps,
            assumptions=assumptions
        )


def _make_plan_id() -> str:
    return f"plan_{int(time.time())}"


class Planner:
    """使用 LLM 将任务+仓库上下文转化为 Plan"""

    def __init__(self, adapter: BaseAdapter):
        self.adapter = adapter
        self.validator = PlanValidator()

    def plan(self, task: str, repo: RepoContext) -> tuple[Plan, dict]:
        """生成 Plan + 原始 LLM 输出（含 content/old_text/new_text）"""
        files_summary = "\n".join(repo.file_tree[:80])
        if len(repo.file_tree) > 80:
            files_summary += f"\n... 还有 {len(repo.file_tree) - 80} 个文件"

        user_prompt = f"""仓库文件列表:
{files_summary}

用户任务: {task}

请输出执行计划的 JSON:"""

        messages = [
            Message(role="system", content=PLANNER_SYSTEM_PROMPT),
            Message(role="user", content=user_prompt)
        ]

        print("[Planner] 正在调用 LLM 生成计划...", file=sys.stderr)
        response = self.adapter.send(messages, tools=[])
        raw = (response.content or "").strip()
        print(f"[Planner] LLM 响应长度: {len(raw)} 字符", file=sys.stderr)

        plan_dict = self._extract_json(raw)
        if plan_dict is None:
            raise PlanValidationError(f"无法从 LLM 响应中提取 JSON:\n{raw[:500]}")

        # 保留原始 dict（含 content/old_text/new_text）
        plan = self.validator.validate(plan_dict, repo)
        return plan, plan_dict

    def _extract_json(self, raw: str) -> Optional[dict]:
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass
        match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', raw, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1).strip())
            except json.JSONDecodeError:
                pass
        start = raw.find('{')
        end = raw.rfind('}')
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(raw[start:end+1])
            except json.JSONDecodeError:
                pass
        return None


def plan_to_proposals(plan: Plan, raw_plan_dict: dict = None) -> list:
    """把 Plan 的每个 Step 转换为 ChangeProposal，附带操作内容"""
    from forge.contracts.constitution import ChangeProposal

    # 建立 step_id → raw_step 的映射
    raw_steps = {}
    if raw_plan_dict:
        for s in raw_plan_dict.get("steps", []):
            raw_steps[s.get("step_id", "")] = s

    proposals = []
    for step in plan.steps:
        raw = raw_steps.get(step.step_id, {})
        proposal = ChangeProposal(
            proposal_id=f"{plan.plan_id}_{step.step_id}",
            plan_id=plan.plan_id,
            target_files=step.target_files,
            operations=[{
                "type": step.operation_type,
                "desc": step.description,
                "step_id": step.step_id,
                "target_files": step.target_files,
                "dependencies": step.dependencies,
                "content": raw.get("content", ""),
                "old_text": raw.get("old_text", ""),
                "new_text": raw.get("new_text", ""),
            }],
            reason=f"{plan.goal} — {step.description}",
            expected_effects=[f"{step.operation_type}: {', '.join(step.target_files)}"]
        )
        proposals.append(proposal)

    return proposals

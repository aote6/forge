"""Planner — LLM 输出行号+新内容 → Forge 从仓库提取 old_text → Validator 校验

原则：old_text 永远来自真实仓库，LLM 只负责"决定改哪里"。
"""
import json
import re
import sys
import time
import os
from typing import Optional

from forge.protocols.repository import RepoContext
from forge.protocols.planning import Plan, PlanStep
from forge.adapters.base import BaseAdapter, Message

PLANNER_SYSTEM_PROMPT = """你是一个代码规划器。你会收到仓库文件列表、文件内容（带行号）、以及用户任务。

输出严格的 JSON，不要包含任何其他文字：
{
  "goal": "用户任务的简洁重述",
  "assumptions": ["假设1"],
  "steps": [
    {
      "step_id": "step_1",
      "description": "做什么",
      "target_files": ["文件路径"],
      "operation_type": "modify | create_file | delete_file",
      "dependencies": [],
      "content": "create_file 时的完整文件内容",
      "start_line": modify 时修改的起始行号 (从1开始),
      "end_line": modify 时修改的结束行号 (含),
      "new_text": "modify 时，替换 start_line 到 end_line 的新内容"
    }
  ]
}

规则：
1. modify 时必须提供 start_line 和 end_line，指向上面文件内容中的行号
2. modify 时必须提供 new_text，即替换后的新内容
3. create_file 时必须提供 content，即完整的新文件内容
4. start_line/end_line 必须在文件行数范围内
5. 只输出 JSON，不要输出任何解释文字
"""


class PlanValidationError(Exception):
    pass


class PlanValidator:
    """校验 Plan 合法性，从仓库提取 old_text"""

    def __init__(self, project_root: str = "."):
        self.project_root = project_root

    def validate(self, plan_dict: dict, repo: RepoContext) -> tuple[Plan, dict]:
        """校验并补充 old_text。返回 (Plan, enriched_dict)"""
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
        enriched_steps = []

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

            enriched = dict(s)  # 复制原始数据

            for tf in targets:
                if op in ("modify", "delete_file"):
                    if tf not in existing_files:
                        raise PlanValidationError(f"{sid}: {op} 的目标 '{tf}' 不在仓库中")
                elif op == "create_file":
                    if tf in existing_files:
                        raise PlanValidationError(f"{sid}: create_file 的目标 '{tf}' 已存在")

            if op == "create_file":
                content = s.get("content", "")
                if not content:
                    raise PlanValidationError(f"{sid}: create_file 缺少 content")

            elif op == "modify":
                start_line = s.get("start_line")
                end_line = s.get("end_line")
                if start_line is None or end_line is None:
                    raise PlanValidationError(f"{sid}: modify 缺少 start_line 或 end_line")
                if not isinstance(start_line, int) or not isinstance(end_line, int):
                    raise PlanValidationError(f"{sid}: start_line/end_line 必须是整数")
                if start_line < 1 or end_line < start_line:
                    raise PlanValidationError(f"{sid}: 无效行号范围 {start_line}-{end_line}")

                new_text = s.get("new_text", "")
                # new_text 可以为空（删除行）

                # 从仓库提取 old_text
                for tf in targets:
                    filepath = os.path.join(self.project_root, tf)
                    if not os.path.exists(filepath):
                        raise PlanValidationError(f"{sid}: 文件不存在: {tf}")
                    with open(filepath, "r", encoding="utf-8") as f:
                        lines = f.readlines()
                    if end_line > len(lines):
                        raise PlanValidationError(
                            f"{sid}: end_line={end_line} 超出文件行数 {len(lines)}"
                        )
                    # 提取 old_text（保留原始格式）
                    old_lines = lines[start_line-1:end_line]
                    old_text = "".join(old_lines)
                    enriched["old_text"] = old_text
                    enriched["target_file_content"] = "".join(lines)

            elif op == "delete_file":
                for tf in targets:
                    filepath = os.path.join(self.project_root, tf)
                    if not os.path.exists(filepath):
                        raise PlanValidationError(f"{sid}: 要删除的文件不存在: {tf}")

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
            enriched_steps.append(enriched)

        for step in steps:
            for dep in step.dependencies:
                if dep not in step_ids:
                    raise PlanValidationError(f"{step.step_id}: 依赖的 {dep} 不存在")

        plan = Plan(
            plan_id=_make_plan_id(),
            goal=goal,
            steps=steps,
            assumptions=assumptions
        )
        enriched_plan = dict(plan_dict)
        enriched_plan["steps"] = enriched_steps
        return plan, enriched_plan


def _make_plan_id() -> str:
    return f"plan_{int(time.time())}"


class Planner:
    """LLM 输出行号+新内容 → Forge 提取 old_text → Validator"""

    def __init__(self, adapter: BaseAdapter):
        self.adapter = adapter

    def plan(self, task: str, repo: RepoContext, project_root: str = ".") -> tuple[Plan, dict]:
        self.validator = PlanValidator(project_root)

        files_summary = "\n".join(repo.file_tree[:80])
        if len(repo.file_tree) > 80:
            files_summary += f"\n... 还有 {len(repo.file_tree) - 80} 个文件"

        # 读取文件内容，带行号
        file_contents = ""
        total_chars = 0
        for f in repo.file_tree[:30]:
            if total_chars > 8000:
                break
            filepath = os.path.join(project_root, f)
            if not os.path.exists(filepath):
                continue
            try:
                with open(filepath, "r", encoding="utf-8") as fh:
                    lines = fh.readlines()
                numbered = "".join(f"{i+1:04d}  {line}" for i, line in enumerate(lines[:100]))
                file_contents += f"\n--- {f} (lines 1-{min(len(lines),100)}) ---\n{numbered}\n"
                total_chars += len(numbered)
            except Exception:
                pass

        user_prompt = f"""仓库文件列表:
{files_summary}

文件内容（带行号，用于精确定位修改位置）:
{file_contents}

用户任务: {task}

请输出执行计划的 JSON（modify 时用 start_line/end_line 指定行号，new_text 指定新内容）:"""

        messages = [
            Message(role="system", content=PLANNER_SYSTEM_PROMPT),
            Message(role="user", content=user_prompt)
        ]

        print("[Planner] 正在调用 LLM 生成计划...", file=sys.stderr)
        response = self.adapter.send(messages, tools=[])
        raw_text = (response.content or "").strip()
        print(f"[Planner] LLM 响应长度: {len(raw_text)} 字符", file=sys.stderr)

        plan_dict = self._extract_json(raw_text)
        if plan_dict is None:
            raise PlanValidationError(f"无法从 LLM 响应中提取 JSON:\n{raw_text[:500]}")

        plan, enriched = self.validator.validate(plan_dict, repo)
        return plan, enriched

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
    from forge.protocols.constitution import ChangeProposal

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
                "start_line": raw.get("start_line"),
                "end_line": raw.get("end_line"),
            }],
            reason=f"{plan.goal} — {step.description}",
            expected_effects=[f"{step.operation_type}: {', '.join(step.target_files)}"]
        )
        proposals.append(proposal)
    return proposals

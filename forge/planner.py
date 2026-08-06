"""Planner — LLM生成草案 + Validator + Repair → Executable Plan"""
import json
import re
import sys
import time
from difflib import SequenceMatcher
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
3. modify 时必须提供 old_text 和 new_text
4. create_file 时必须提供 content
5. old_text 尽量简短且唯一，方便精确定位
6. 只输出 JSON，不要输出任何解释文字
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

            if op == "create_file":
                content = s.get("content", "")
                if not content:
                    raise PlanValidationError(f"{sid}: create_file 缺少 content")
            elif op == "modify":
                old_text = s.get("old_text", "")
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


class PlanRepair:
    """修复 LLM 生成的 old_text，使其精确匹配文件内容"""

    @staticmethod
    def repair(raw_plan: dict, project_root: str) -> dict:
        """遍历所有 modify step，用模糊匹配修正 old_text"""
        import os
        repaired = json.loads(json.dumps(raw_plan))  # deep copy
        for step in repaired.get("steps", []):
            if step.get("operation_type") != "modify":
                continue
            old_text = step.get("old_text", "")
            if not old_text:
                continue
            for target in step.get("target_files", []):
                filepath = os.path.join(project_root, target)
                if not os.path.exists(filepath):
                    continue
                with open(filepath, "r", encoding="utf-8") as f:
                    content = f.read()
                if old_text in content:
                    continue  # 已经精确匹配，不需要修复

                # 模糊匹配
                fixed = PlanRepair._fuzzy_find(old_text, content)
                if fixed:
                    print(f"  [Repair] old_text 修复: {repr(old_text[:40])} -> {repr(fixed[:40])}", file=sys.stderr)
                    # 同步更新 new_text：保持与修复后 old_text 相同的引号风格
                    new_text = step.get("new_text", "")
                    if new_text:
                        # 尝试用修复后的 old_text 推断 new_text 的格式
                        step["new_text"] = PlanRepair._fix_new_text(old_text, fixed, new_text)
                    step["old_text"] = fixed
                else:
                    print(f"  [Repair] 无法修复 old_text: {repr(old_text[:60])}", file=sys.stderr)
        return repaired

    @staticmethod
    def _fuzzy_find(needle: str, haystack: str) -> Optional[str]:
        """在 haystack 中找与 needle 最相似的子串"""
        if not needle or not haystack:
            return None

        # 先尝试忽略空白差异
        needle_compact = ''.join(needle.split())
        haystack_compact = ''.join(haystack.split())
        if needle_compact in haystack_compact:
            idx = haystack_compact.find(needle_compact)
            # 在原 haystack 中定位
            pos = 0
            compact_pos = 0
            for i, ch in enumerate(haystack):
                if not ch.isspace():
                    if compact_pos == idx:
                        pos = i
                        break
                    compact_pos += 1
            # 提取原 haystack 中对应位置的子串，保持原始格式
            end = pos + len(needle)
            return haystack[pos:end]

        # 逐行匹配
        needle_lines = needle.strip().splitlines()
        haystack_lines = haystack.splitlines()

        best_ratio = 0
        best_match = None

        for i in range(len(haystack_lines) - len(needle_lines) + 1):
            window = '\n'.join(haystack_lines[i:i+len(needle_lines)])
            ratio = SequenceMatcher(None, needle, window).ratio()
            if ratio > best_ratio and ratio > 0.7:
                best_ratio = ratio
                best_match = window

        return best_match
    @staticmethod
    def _fix_new_text(old_original: str, old_fixed: str, new_text: str) -> str:
        """根据 old_text 的修复结果，调整 new_text 的格式"""
        # 如果 old 只是引号/空格差异，直接替换差异部分
        if old_original.replace("'", '"').replace(" ", "") == old_fixed.replace("'", '"').replace(" ", ""):
            # 找到 old_fixed 和 old_original 之间的映射关系
            return new_text.replace(old_original, old_fixed) if old_original in new_text else new_text
        return new_text



def _make_plan_id() -> str:
    return f"plan_{int(time.time())}"


class Planner:
    """LLM 生成草案 + Validator + Repair → Executable Plan"""

    def __init__(self, adapter: BaseAdapter):
        self.adapter = adapter
        self.validator = PlanValidator()

    def plan(self, task: str, repo: RepoContext, project_root: str = ".") -> tuple[Plan, dict]:
        """生成可执行 Plan：LLM → Repair → Validate"""
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
        raw_text = (response.content or "").strip()
        print(f"[Planner] LLM 响应长度: {len(raw_text)} 字符", file=sys.stderr)

        plan_dict = self._extract_json(raw_text)
        if plan_dict is None:
            raise PlanValidationError(f"无法从 LLM 响应中提取 JSON:\n{raw_text[:500]}")

        # Repair: 修正 old_text
        print("[Planner] Repair: 修正 old_text...", file=sys.stderr)
        repaired_dict = PlanRepair.repair(plan_dict, project_root)

        # Validate
        plan = self.validator.validate(repaired_dict, repo)
        return plan, repaired_dict

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
    """把 Plan 的每个 Step 转换为 ChangeProposal"""
    from forge.contracts.constitution import ChangeProposal

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

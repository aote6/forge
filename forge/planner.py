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
from forge.plan_validator import PlanValidator
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


def _make_plan_id() -> str:
    return f"plan_{int(time.time())}"


class Planner:
    """LLM 输出行号+新内容 → Forge 提取 old_text → Validator"""

    def __init__(self, adapter: BaseAdapter):
        self.adapter = adapter

    def plan(
        self,
        task: str,
        repo: RepoContext,
        project_root: str = ".",
        index=None,
        failure=None,
        repair_constraints=None,
    ) -> tuple[Plan, dict]:
        self.validator = PlanValidator(project_root)

        # Full file tree is always provided (names never silently truncated).
        files_summary = "\n".join(repo.file_tree) if repo and repo.file_tree else "(empty repo)"

        # Prefer changed_files then rest of tree for content; tree listing is complete.
        content_candidates = list(dict.fromkeys(
            list((repo.changed_files if repo else None) or []) + list((repo.file_tree if repo else None) or [])
        ))
        file_contents = ""
        total_chars = 0
        MAX_CONTENT_CHARS = 48000
        for f in content_candidates:
            if total_chars > MAX_CONTENT_CHARS:
                file_contents += (
                    f"\n... content budget reached; remaining files listed in tree only "
                    f"({len(content_candidates)} candidates)\n"
                )
                break
            filepath = os.path.join(project_root, f)
            if not os.path.exists(filepath):
                continue
            try:
                with open(filepath, "r", encoding="utf-8") as fh:
                    lines = fh.readlines()
                limit = 400 if len(lines) > 400 else len(lines)
                numbered = "".join(f"{i+1:04d}  {line}" for i, line in enumerate(lines[:limit]))
                suffix = "" if limit == len(lines) else f" (showing 1-{limit} of {len(lines)})"
                file_contents += f"\n--- {f}{suffix} ---\n{numbered}\n"
                total_chars += len(numbered)
            except Exception:
                pass

        # Priority 2: structured repository model from index (not str(index)).
        model_section = "(no repository index)"
        focus = []
        if index is not None:
            from forge.context.index import extract_focus_symbols
            focus = extract_focus_symbols(task)
            model_section = index.summary_for_planner(focus_symbols=focus or None)

        failure_section = "(no structured failure)"
        if failure is not None:
            fd = failure.to_dict() if hasattr(failure, "to_dict") else dict(failure)
            failure_section = (
                f"code={fd.get('code')}\n"
                f"message={fd.get('message')}\n"
                f"files={fd.get('files')}\n"
                f"signature={fd.get('signature')}\n"
                f"evidence_keys={list((fd.get('evidence') or {}).keys())}"
            )
        constraints_section = "(none)"
        if repair_constraints is not None:
            cd = repair_constraints.to_dict() if hasattr(repair_constraints, "to_dict") else dict(repair_constraints)
            constraints_section = str(cd)

        user_prompt = f"""仓库文件列表:
{files_summary}

Repository model (machine facts — prefer over guessing):
{model_section}

Structured failure (machine classified — repair must address this):
{failure_section}

Repair constraints (machine enforced by validator):
{constraints_section}

文件内容（带行号，用于精确定位修改位置）:
{file_contents}

用户任务: {task}

请输出执行计划的 JSON（modify 时用 start_line/end_line 指定行号，new_text 指定新内容）。
若任务涉及已知 symbol，JSON 可含 impact_files（受影响文件列表）与 impact_symbols。
modify/delete 的 target_files 必须落在 impact_files 内（若提供了 impact_files）。
create_file 不受 impact_files 限制。:"""

        messages = [
            Message(role="system", content=PLANNER_SYSTEM_PROMPT),
            Message(role="user", content=user_prompt)
        ]

        print("[Planner] 正在调用 LLM 生成计划...", file=sys.stderr)
        print(f"[Planner DEBUG] system prompt len={len(PLANNER_SYSTEM_PROMPT)}", file=sys.stderr)
        print(f"[Planner DEBUG] user prompt len={len(user_prompt)}", file=sys.stderr)
        print(f"[Planner DEBUG] user prompt:\n{user_prompt[:2000]}", file=sys.stderr)
        print(f"[Planner DEBUG] total messages: {len(messages)}", file=sys.stderr)
        response = self.adapter.send(messages, tools=[])
        print(f"[Planner DEBUG] raw response len={len(response.content or '')}", file=sys.stderr)
        print(f"[Planner DEBUG] raw response:\n{repr(response.content[:500])}", file=sys.stderr)
        raw_text = (response.content or "").strip()
        print(f"[Planner] LLM 响应长度: {len(raw_text)} 字符", file=sys.stderr)

        plan_dict = self._extract_json(raw_text)
        if plan_dict is None:
            print(f"[Planner] 无法提取 JSON, 原始响应:\n{raw_text}", file=sys.stderr)
            raise PlanValidationError(f"无法从 LLM 响应中提取 JSON:\n{raw_text[:500]}")

        plan, enriched = self.validator.validate(plan_dict, repo, repair_constraints=repair_constraints)
        # Priority 2: if index present and plan lacks impact_files, derive from focus symbols.
        if index is not None and not plan.impact_files:
            from forge.context.index import extract_focus_symbols
            symbols = list(plan.impact_symbols) or extract_focus_symbols(task)
            files: set[str] = set()
            used_syms = []
            for name in symbols:
                aff = index.affected_files(name)
                if aff:
                    files.update(aff)
                    used_syms.append(name)
            if files:
                plan.impact_files = sorted(files)
                plan.impact_symbols = used_syms
                enriched["impact_files"] = plan.impact_files
                enriched["impact_symbols"] = plan.impact_symbols
                # Re-check boundary after enrichment
                allowed = set(plan.impact_files)
                for step in plan.steps:
                    if step.operation_type in ("modify", "delete_file", "delete"):
                        for tf in step.target_files:
                            if tf not in allowed:
                                raise PlanValidationError(
                                    f"{step.step_id}: target '{tf}' outside "
                                    f"index-derived impact_files {sorted(allowed)}"
                                )
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
    """Canonical converter lives in orchestrator.engine; re-export for callers."""
    from forge.orchestrator.engine import plan_to_proposals as _canonical
    return _canonical(plan)

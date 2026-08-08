"""Planner — LLM 输出行号+新内容 → Forge 从仓库提取 old_text → Validator 校验

原则：
- old_text 永远来自真实仓库，LLM 只负责"决定改哪里"。
- impact / callers / ambiguous symbols 由 RepositoryIndex 机器推导，
  再与 LLM 输出合并；Validator 是最终硬门槛。
- step dependencies 经拓扑排序后再执行。
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
from forge.context.planning import (
    apply_machine_impact_to_plan,
    compute_impact_set,
    compute_obligations,
    extract_task_paths,
    format_file_with_line_numbers,
    format_impact_section,
    format_obligations_section,
    select_planning_content_files,
)

PLANNER_SYSTEM_PROMPT = """你是一个代码规划器。你会收到仓库文件列表、机器推导的 impact 信息、文件内容（带行号）、以及用户任务。

输出严格的 JSON，不要包含任何其他文字：
{
  "goal": "用户任务的简洁重述",
  "assumptions": ["假设1"],
  "impact_files": ["机器或任务相关的受影响文件"],
  "impact_symbols": ["相关 symbol 名"],
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
6. modify/delete 的 target_files 必须落在 impact_files 内（若提供了 impact_files）
7. create_file 不受 impact_files 限制
8. 多文件 API 修改时：先改 definition，再改 callers，再改 tests；用 dependencies 表达顺序
9. 对 AMBIGUOUS symbols，不得擅自选择错误定义文件；只改任务明确指向的文件
10. 不要修改 Machine impact set 之外的无关文件，除非任务明确要求 create_file
"""


class PlanValidationError(Exception):
    pass


def _make_plan_id() -> str:
    return f"plan_{int(time.time())}"


class Planner:
    """LLM 输出行号+新内容 → Forge 提取 old_text → Validator + machine impact."""

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

        # Machine impact from P2 index (before LLM).
        seed_files = []
        if repair_constraints is not None:
            rc = repair_constraints
            if hasattr(rc, "must_touch_files"):
                seed_files.extend(list(rc.must_touch_files or []))
                seed_files.extend(list(rc.required_impact_files or []))
                seed_files.extend(list(rc.force_create_files or []))
            elif isinstance(rc, dict):
                seed_files.extend(list(rc.get("must_touch_files") or []))
                seed_files.extend(list(rc.get("required_impact_files") or []))
                seed_files.extend(list(rc.get("force_create_files") or []))
        if failure is not None:
            fd = failure.to_dict() if hasattr(failure, "to_dict") else dict(failure)
            seed_files.extend(list(fd.get("files") or []))

        # Paths named in the task are seed impact (so boundary includes explicit targets).
        task_paths = extract_task_paths(task)
        seed_files = list(seed_files or []) + task_paths
        # When the user names explicit files, scope symbol focus to definitions
        # inside those files — avoid treating quoted instruction text (e.g.
        # "EngineeringOrchestrator") as a global refactor obligation.
        focus_symbols = None
        if task_paths and index is not None:
            from forge.context.index import extract_focus_symbols
            scoped = []
            for name in extract_focus_symbols(task):
                defs = index.find_definition(name)
                if any(getattr(d, "file_path", None) in task_paths for d in defs):
                    scoped.append(name)
            focus_symbols = scoped  # may be empty — still OK with path seeds
        machine = compute_impact_set(
            index,
            task=task if focus_symbols is None else "",
            focus_symbols=focus_symbols,
            seed_files=seed_files or None,
        )
        # Ensure explicit task paths remain in impact even if no symbols matched
        if task_paths:
            files = set(machine.get("impact_files") or [])
            files.update(task_paths)
            machine["impact_files"] = sorted(files)
        obligations = compute_obligations(
            index,
            task=task if focus_symbols is None else "",
            focus_symbols=focus_symbols,
            machine=machine,
            repair_constraints=repair_constraints,
        )
        impact_section = format_impact_section(machine)
        obligations_section = format_obligations_section(obligations)

        # File *names* only in the tree listing (never dump whole-repo source).
        files_summary = "\n".join(repo.file_tree) if repo and repo.file_tree else "(empty repo)"

        # Source injection: ONLY planning-relevant files (task paths / obligations / impact).
        # Do NOT iterate the entire repository — that exhausts budget before real targets.
        content_candidates = select_planning_content_files(
            task=task,
            impact_files=list(machine.get("impact_files") or []),
            obligations=obligations,
            file_tree=None,
            max_secondary=0,
        )
        for pth in extract_task_paths(task):
            if pth not in content_candidates:
                content_candidates.insert(0, pth)

        file_contents = ""
        injected_files: list[str] = []
        total_chars = 0
        MAX_CONTENT_CHARS = 80000
        PER_FILE_MAX_CHARS = 60000
        for f in content_candidates:
            if total_chars >= MAX_CONTENT_CHARS:
                file_contents += (
                    f"\n... content budget reached after {len(injected_files)} target files; "
                    f"remaining candidates listed by name only: "
                    f"{content_candidates[len(injected_files):]}\n"
                )
                break
            remaining = MAX_CONTENT_CHARS - total_chars
            block = format_file_with_line_numbers(
                project_root,
                f,
                max_lines=2500,
                max_chars=min(PER_FILE_MAX_CHARS, remaining),
            )
            if not block:
                file_contents += (
                    f"\n=== TARGET FILE: {f} ===\n"
                    f"(file not present on disk — create_file or missing)\n"
                )
                continue
            file_contents += block
            injected_files.append(f)
            total_chars += len(block)

        # Priority 2: structured repository model from index (not str(index)).
        model_section = "(no repository index)"
        if index is not None:
            focus = list(machine.get("impact_symbols") or [])
            if not focus:
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

        # Seed impact into expected JSON fields for the LLM.
        seeded_impact = machine.get("impact_files") or []
        seeded_symbols = machine.get("impact_symbols") or []

        user_prompt = f"""仓库文件列表:
{files_summary}

Repository model (machine facts — prefer over guessing):
{model_section}

{impact_section}

{obligations_section}

Structured failure (machine classified — repair must address this):
{failure_section}

Repair constraints (machine enforced by validator):
{constraints_section}

=== PLANNING TARGET CONTEXT (real source, line-numbered; authoritative) ===
{file_contents}

用户任务: {task}

请输出执行计划的 JSON。
机器已推导 impact_files 候选: {seeded_impact}
机器已推导 impact_symbols 候选: {seeded_symbols}
请在 JSON 中填写 impact_files / impact_symbols（可补充，不可无理由缩小到遗漏已知 callers）。
modify/delete 的 target_files 必须落在 impact_files 内。
REQUIRED obligations 的 file 必须出现在某个 step 的 target_files 中（机器强制）。
多步骤时用 dependencies 表达「先 definition 后 callers 后 tests」。
create_file 不受 impact_files 限制。"""

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
        print(f"[Planner DEBUG] raw response:\n{repr((response.content or '')[:500])}", file=sys.stderr)
        raw_text = (response.content or "").strip()
        print(f"[Planner] LLM 响应长度: {len(raw_text)} 字符", file=sys.stderr)

        plan_dict = self._extract_json(raw_text)
        if plan_dict is None:
            print(f"[Planner] 无法提取 JSON, 原始响应:\n{raw_text}", file=sys.stderr)
            raise PlanValidationError(f"无法从 LLM 响应中提取 JSON:\n{raw_text[:500]}")

        # Pre-seed impact into plan_dict so Validator sees a boundary even if LLM omitted it.
        if seeded_impact and not plan_dict.get("impact_files"):
            plan_dict["impact_files"] = list(seeded_impact)
        if seeded_symbols and not plan_dict.get("impact_symbols"):
            plan_dict["impact_symbols"] = list(seeded_symbols)

        plan, enriched = self.validator.validate(
            plan_dict,
            repo,
            repair_constraints=repair_constraints,
            obligations=obligations,
        )

        # Always merge machine impact (union) and topologically order steps.
        apply_machine_impact_to_plan(plan, machine)
        enriched["impact_files"] = list(plan.impact_files)
        enriched["impact_symbols"] = list(plan.impact_symbols)
        enriched["machine_impact"] = {
            "impact_files": machine.get("impact_files"),
            "impact_symbols": machine.get("impact_symbols"),
            "ambiguous_symbols": machine.get("ambiguous_symbols"),
            "callers_by_symbol": machine.get("callers_by_symbol"),
        }
        enriched["obligations"] = list(obligations)
        enriched["injected_source_files"] = list(injected_files)
        enriched["planning_content_candidates"] = list(content_candidates)

        # Re-check modify/delete boundary after merge.
        if plan.impact_files:
            allowed = set(plan.impact_files)
            for step in plan.steps:
                if step.operation_type in ("modify", "delete_file", "delete"):
                    for tf in step.target_files:
                        if tf not in allowed:
                            raise PlanValidationError(
                                f"{step.step_id}: target '{tf}' outside "
                                f"merged impact_files {sorted(allowed)}"
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

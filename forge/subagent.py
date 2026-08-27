"""Subagent — isolated tool-loop for exploration / focused edits.

Main agent calls spawn_subagent(task); the subagent runs with a reduced tool
surface and its own messages list. The executor returns an AgentResult
(machine status), never raw model text as the protocol result.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import uuid

from forge.adapters.base import BaseAdapter, Message, ToolCall, ToolResult
from forge.agent_abi import (
    AgentResult,
    AgentTask,
    assemble_agent_result,
    build_subagent_user_message,
    parse_candidate,
)
from forge.constraint_enforcer import enforce
from forge.core.sanitizer import sanitize_and_redact
from forge.tools.schemas import EXECUTION_PLANE_TOOLS
from forge.events import Event, EventType
from forge.execution_gate import (
    ALLOW,
    PAUSE,
    VERIFY_SIDE_EFFECT_PATTERNS,
    VERIFY_TOOL_NAMES,
    classify_for_confirmation,
    pause_summary,
)
from forge.tool_call_record import (
    ToolCallRecord,
    current_timestamp,
    new_tool_call_id,
    write_record,
)

SUBAGENT_MAX_STEPS = 15

# 固定四段式结论结构。主循环只消费这个摘要，绝不消费子代理的工具轨迹。
_SUBAGENT_SECTIONS = ("CONCLUSION", "EVIDENCE", "UNCERTAIN", "NEXT")
_SECTION_RE = re.compile(
    r"^\s*(CONCLUSION|EVIDENCE|UNCERTAIN|NEXT)\s*:\s*(.*)$", re.IGNORECASE
)
_EMPTY_MARK = "(无)"

# 循环控制信号：每轮 content 中的显式 STOP_WHEN 行（非整段自然语言）。
_STOP_WHEN_RE = re.compile(
    r"^\s*STOP_WHEN\s*:\s*(met|not_met)\s*$", re.IGNORECASE | re.MULTILINE
)

SUBAGENT_SYSTEM = """你是 Forge 子 Agent。完成主 Agent 交给你的子任务。
- 用工具探索与必要的小修改（str_replace / write_file）。
- 不要无限搜索；找到结论后停止调用工具。
- 每一轮回复（无论是否调用工具）必须单独包含一行循环控制信号，格式固定为其一：
  STOP_WHEN: not_met
  STOP_WHEN: met
  当 STOP_WHEN: met 时，本轮之后禁止再请求任何工具；直接给出最终结论。
- 工具结果会带 tool_call_id=... 行；写 EVIDENCE 时必须引用真实的 tool_call_id。
- 最终回复必须严格使用下面的固定格式，不要贴完整文件内容，不要复述搜索过程：

CONCLUSION:
<一句话结论：发现了什么 / 改了什么>

EVIDENCE:
- tool_call_id=<id> path=<path> <一行关键原文或证据>

UNCERTAIN:
<不确定、未验证、待确认的点；没有就写 (无)>

NEXT:
<建议主 Agent 的下一步；没有就写 (无)>
"""


def structure_conclusion(text: str) -> str:
    """把子代理最终回复强制收敛成四段式结论结构。

    - 已含段落标记：保留各段内容，缺失段补 "(无)"。
    - 无标记的自由文本：整体当作 CONCLUSION，其余段补 "(无)"。
    只做形状规整，绝不凭空编造 evidence / next。
    输出是候选文本，不是最终 AgentResult。
    """
    text = (text or "").strip()
    if not text:
        return f"CONCLUSION:\n(subagent: empty conclusion)\n"

    content: dict[str, list[str]] = {name: [] for name in _SUBAGENT_SECTIONS}
    seen: list[str] = []
    current: str | None = None
    for raw_line in text.splitlines():
        m = _SECTION_RE.match(raw_line)
        if m:
            current = m.group(1).upper()
            if current not in seen:
                seen.append(current)
            rest = m.group(2).strip()
            if rest:
                content[current].append(rest)
            continue
        if current is not None:
            content[current].append(raw_line.rstrip())

    if not seen:
        content["CONCLUSION"] = [ln.rstrip() for ln in text.splitlines()]

    out: list[str] = []
    for name in _SUBAGENT_SECTIONS:
        body = "\n".join(ln for ln in content[name] if ln.strip()).strip()
        out.append(f"{name}:")
        out.append(body if body else _EMPTY_MARK)
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def parse_stop_when(content: str) -> str:
    """Extract explicit STOP_WHEN signal from one model turn.

    Returns "met" or "not_met". Missing / invalid → "not_met".
    If multiple lines match, the last one wins.
    """
    text = content or ""
    found = "not_met"
    for m in _STOP_WHEN_RE.finditer(text):
        found = m.group(1).lower()
    return found


def strip_stop_when(content: str) -> str:
    """Remove STOP_WHEN control lines before conclusion structuring."""
    text = content or ""
    return _STOP_WHEN_RE.sub("", text)



def _layer_b_snapshot(
    project_root: str | os.PathLike,
    args: dict,
    tool_name: str | None = None,
) -> dict[str, str]:
    """Minimal deterministic snapshot of paths referenced by a tool call.

    For VERIFY tools the snapshot also covers authorized side-effect paths,
    so Layer B can tell authorized cache/persistence writes apart from
    unauthorized engineering-world changes.
    """
    import glob as _glob

    root = Path(project_root)
    paths: list[str] = []
    for key in ("path", "file", "target"):
        v = args.get(key)
        if isinstance(v, str) and v.strip():
            paths.append(v.strip())
    edits = args.get("edits")
    if isinstance(edits, list):
        for e in edits:
            if isinstance(e, dict):
                p = e.get("path") or e.get("file")
                if isinstance(p, str) and p.strip():
                    paths.append(p.strip())
    # VERIFY side-effect coverage: materialize glob patterns under project_root.
    if tool_name in VERIFY_TOOL_NAMES:
        for pattern in VERIFY_SIDE_EFFECT_PATTERNS.get(tool_name, frozenset()):
            full_pattern = str(root / pattern)
            for hit in _glob.glob(full_pattern, recursive=True):
                rel = str(Path(hit).relative_to(root))
                if rel not in paths:
                    paths.append(rel)
    snap: dict[str, str] = {}
    for p in paths:
        fp = root / p if not os.path.isabs(p) else Path(p)
        try:
            if fp.is_file():
                st = fp.stat()
                snap[p] = f"{st.st_mtime_ns}:{st.st_size}"
            elif fp.exists():
                snap[p] = "exists"
            else:
                snap[p] = "missing"
        except OSError:
            snap[p] = "error"
    return snap


def _layer_b_changed(before: dict[str, str], after: dict[str, str]) -> bool:
    return before != after


def _layer_b_unauthorized_diff(
    project_root: str | os.PathLike,
    before: dict[str, str],
    after: dict[str, str],
    authorized_patterns: frozenset[str],
) -> list[str]:
    """Return changed paths that are NOT covered by authorized side-effect patterns."""
    import fnmatch

    root = Path(project_root)
    changed: list[str] = []
    for p in after:
        if before.get(p) != after.get(p):
            if os.path.isabs(p):
                rel = p
            else:
                try:
                    rel = str(Path(p).relative_to(root))
                except ValueError:
                    rel = p
            allowed = False
            for pat in authorized_patterns:
                if fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(p, pat):
                    allowed = True
                    break
            if not allowed:
                changed.append(rel)
    return changed


def filter_schemas_for_subagent(all_schemas: list[dict]) -> list[dict]:
    """Keep only execution-plane tools. Control-plane names are dropped."""
    return [s for s in all_schemas if s.get("name") in EXECUTION_PLANE_TOOLS]


def _execute_tool(
    tools: dict,
    tc: ToolCall,
    *,
    project_root: str | os.PathLike,
    subtask_id: str,
    records_out: list[ToolCallRecord],
) -> tuple[ToolResult, str | None]:
    """Execute one tool; append ToolCallRecord on real invocation.

    Returns (ToolResult, tool_call_id|None). tool_call_id is set when a
    record was written so the model can cite it in EVIDENCE.
    """
    fn = tools.get(tc.name)
    if fn is None:
        return ToolResult.fail(display=f"subagent: unknown tool {tc.name}"), None

    tool_call_id = new_tool_call_id()
    args = tc.arguments if isinstance(tc.arguments, dict) else {}

    try:
        result = fn(**args)
        status = "success" if getattr(result, "success", False) else "error"
        error = None if status == "success" else getattr(result, "display", None)
        output = getattr(result, "payload", None)
        record_result = result
    except TypeError as e:
        status = "error"
        error = f"arg error: {e}"
        output = None
        record_result = ToolResult.fail(display=f"subagent tool arg error ({tc.name}): {e}")
    except Exception as e:
        status = "error"
        error = f"exception: {e}"
        output = None
        record_result = ToolResult.fail(display=f"subagent tool failed ({tc.name}): {e}")

    record = ToolCallRecord(
        tool_call_id=tool_call_id,
        subtask_id=subtask_id,
        tool_name=tc.name,
        input=args,
        output=output,
        status=status,
        error=error,
        timestamp=current_timestamp(),
    )
    write_record(project_root, record)
    records_out.append(record)

    return record_result, tool_call_id


def _finalize(
    task: AgentTask,
    *,
    subtask_id: str,
    last_text: str,
    stop_when_met: bool,
    exit_kind: str,
    records: list[ToolCallRecord],
    error_message: str = "",
) -> AgentResult:
    structured = structure_conclusion(
        strip_stop_when(last_text)
        or (
            "(subagent: reached max steps without final text; "
            "check partial tool results in logs)"
            if exit_kind == "max_steps"
            else ""
        )
    )
    candidate = parse_candidate(
        structured,
        stop_when_met=stop_when_met,
        exit_kind=exit_kind,
        error_message=error_message,
    )
    result = assemble_agent_result(
        task, candidate, records, subtask_id=subtask_id
    )
    return AgentResult(
        subtask_id=result.subtask_id,
        status=result.status,
        conclusion=result.conclusion,
        evidence=result.evidence,
        uncertain=result.uncertain,
        next=result.next,
        stop_when_met=result.stop_when_met,
        status_reason=result.status_reason,
        raw_conclusion=structured,
    )


def run_subagent(
    adapter: BaseAdapter,
    tools: dict,
    schemas: list[dict],
    task: AgentTask,
    *,
    project_root: str | os.PathLike = ".",
    confirm_fn=None,
    emit=None,
) -> AgentResult:
    """Run an isolated tool loop; return machine-assembled AgentResult."""
    if not isinstance(task, AgentTask):
        raise TypeError(
            f"run_subagent expects AgentTask, got {type(task).__name__}"
        )

    subtask_id = task.subtask_id or f"sub_{uuid.uuid4().hex[:12]}"
    max_steps = int(task.max_steps) if task.max_steps else SUBAGENT_MAX_STEPS
    if max_steps < 1:
        max_steps = SUBAGENT_MAX_STEPS
    constraints = task.constraints if isinstance(task.constraints, dict) else {}

    schemas = filter_schemas_for_subagent(schemas)
    tool_names = {s["name"] for s in schemas}
    tools = {k: v for k, v in tools.items() if k in tool_names}

    messages = [
        Message(role="system", content=SUBAGENT_SYSTEM),
        Message(role="user", content=build_subagent_user_message(task)),
    ]
    last_text = ""
    records: list[ToolCallRecord] = []

    try:
        for _ in range(max_steps):
            resp = adapter.send(messages, schemas)
            content = resp.content or ""
            signal = parse_stop_when(content)

            # Hard stop: stop_when met → discard this turn's tool_calls, no next round.
            if signal == "met":
                last_text = content.strip()
                return _finalize(
                    task,
                    subtask_id=subtask_id,
                    last_text=last_text,
                    stop_when_met=True,
                    exit_kind="stop_when",
                    records=records,
                )

            if not resp.tool_calls:
                last_text = content.strip()
                return _finalize(
                    task,
                    subtask_id=subtask_id,
                    last_text=last_text,
                    stop_when_met=False,
                    exit_kind="no_tools",
                    records=records,
                )

            messages.append(
                Message(
                    role="assistant",
                    content=resp.content,
                    tool_calls=resp.tool_calls,
                )
            )
            for tc in resp.tool_calls:
                args = tc.arguments if isinstance(tc.arguments, dict) else {}
                decision = enforce(tc.name, args, constraints)
                if not decision.allowed:
                    messages.append(
                        Message(
                            role="tool",
                            content=sanitize_and_redact(
                                f"constraint denied: {decision.reason}"
                            ),
                            tool_call_id=getattr(tc, "id", None),
                            name=tc.name,
                        )
                    )
                    continue

                # --- Execution Gate: confirmation layer (post-enforce) ---
                gate = classify_for_confirmation(tc.name, args)
                confirmed_write = False
                if gate == PAUSE:
                    if confirm_fn is None:
                        last_text = content.strip()
                        return _finalize(
                            task,
                            subtask_id=subtask_id,
                            last_text=last_text,
                            stop_when_met=False,
                            exit_kind="confirmation_unavailable",
                            records=records,
                            error_message="confirmation_unavailable",
                        )
                    summary = pause_summary(tc.name, args)
                    try:
                        approved = bool(confirm_fn(summary))
                    except Exception as e:
                        last_text = content.strip()
                        return _finalize(
                            task,
                            subtask_id=subtask_id,
                            last_text=last_text,
                            stop_when_met=False,
                            exit_kind="confirmation_unavailable",
                            records=records,
                            error_message=f"confirmation_unavailable: {e}",
                        )
                    if not approved:
                        last_text = content.strip()
                        return _finalize(
                            task,
                            subtask_id=subtask_id,
                            last_text=last_text,
                            stop_when_met=False,
                            exit_kind="user_denied_write",
                            records=records,
                            error_message="user_denied_write",
                        )
                    confirmed_write = True

                # Layer B: snapshot before execute.
                # authorized write = confirmed PAUSE, or policy-ALLOW recovery tools.
                authorized_write = confirmed_write or (tc.name in ("undo_last_tx",))
                if emit is not None:
                    try:
                        emit(Event(EventType.TOOL_CALL_START, {"name": tc.name, "args": args}))
                    except Exception:
                        pass
                before = _layer_b_snapshot(project_root, args, tool_name=tc.name)
                result, tool_call_id = _execute_tool(
                    tools,
                    tc,
                    project_root=project_root,
                    subtask_id=subtask_id,
                    records_out=records,
                )
                after = _layer_b_snapshot(project_root, args, tool_name=tc.name)
                if not authorized_write and gate == ALLOW:
                    if tc.name in VERIFY_TOOL_NAMES:
                        # VERIFY: authorized side effects are subtracted from diff.
                        patterns = VERIFY_SIDE_EFFECT_PATTERNS.get(tc.name, frozenset())
                        remaining = _layer_b_unauthorized_diff(
                            project_root, before, after, patterns
                        )
                        if remaining:
                            last_text = content.strip()
                            return _finalize(
                                task,
                                subtask_id=subtask_id,
                                last_text=last_text,
                                stop_when_met=False,
                                exit_kind="unauthorized_world_change",
                                records=records,
                                error_message=(
                                    f"unauthorized_world_change:after_tool={tc.name}"
                                    f" paths={sorted(remaining)[:5]}"
                                ),
                            )
                    elif _layer_b_changed(before, after):
                        # READ_ONLY ALLOW path produced a world change
                        last_text = content.strip()
                        return _finalize(
                            task,
                            subtask_id=subtask_id,
                            last_text=last_text,
                            stop_when_met=False,
                            exit_kind="unauthorized_world_change",
                            records=records,
                            error_message=(
                                f"unauthorized_world_change:after_tool={tc.name}"
                            ),
                        )

                if emit is not None:
                    try:
                        emit(
                            Event(
                                EventType.TOOL_CALL_END,
                                {
                                    "name": tc.name,
                                    "success": getattr(result, "success", False),
                                    "display": result.display or "",
                                },
                            )
                        )
                    except Exception:
                        pass
                display = result.display or ""
                if tool_call_id:
                    display = f"tool_call_id={tool_call_id}\n{display}"
                if decision.advisory_violations:
                    display += "\n[advisory] " + "; ".join(decision.advisory_violations)
                messages.append(
                    Message(
                        role="tool",
                        content=sanitize_and_redact(display),
                        tool_call_id=getattr(tc, "id", None),
                        name=tc.name,
                    )
                )
            if content:
                last_text = content.strip()
        return _finalize(
            task,
            subtask_id=subtask_id,
            last_text=last_text,
            stop_when_met=False,
            exit_kind="max_steps",
            records=records,
        )
    except Exception as e:
        return _finalize(
            task,
            subtask_id=subtask_id,
            last_text=last_text,
            stop_when_met=False,
            exit_kind="error",
            records=records,
            error_message=str(e),
        )

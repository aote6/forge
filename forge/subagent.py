"""Subagent — isolated tool-loop for exploration / focused edits.

Main agent calls spawn_subagent(task); the subagent runs with a reduced tool
surface and its own messages list. Only the final text conclusion is returned
to the parent context (no tool-call history).
"""
from __future__ import annotations

import re
from typing import Any, Callable

from forge.adapters.base import BaseAdapter, Message, ToolCall, ToolResult

SUBAGENT_MAX_STEPS = 15

# 固定四段式结论结构。主循环只消费这个摘要，绝不消费子代理的工具轨迹。
_SUBAGENT_SECTIONS = ("CONCLUSION", "EVIDENCE", "UNCERTAIN", "NEXT")
_SECTION_RE = re.compile(
    r"^\s*(CONCLUSION|EVIDENCE|UNCERTAIN|NEXT)\s*:\s*(.*)$", re.IGNORECASE
)
_EMPTY_MARK = "(无)"

SUBAGENT_SYSTEM = """你是 Forge 子 Agent。完成主 Agent 交给你的子任务。
- 用工具探索与必要的小修改（str_replace / write_file）。
- 不要无限搜索；找到结论后停止调用工具。
- 最终回复必须严格使用下面的固定格式，不要贴完整文件内容，不要复述搜索过程：

CONCLUSION:
<一句话结论：发现了什么 / 改了什么>

EVIDENCE:
- path:line <一行关键原文或证据>

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

# Tool names allowed for subagents (read + minimal write)
SUBAGENT_READ_NAMES = frozenset({
    "read_file", "read_function", "glob_files", "search_code",
    "find_symbol_definition", "get_repo_map", "git_diff",
    "run_command", "run_test_structured", "run_type_check",
    "list_world_objects", "world_info", "get_world_object", "list_world_links",
})
SUBAGENT_MUT_NAMES = frozenset({"str_replace", "write_file"})


def filter_schemas_for_subagent(all_schemas: list[dict]) -> list[dict]:
    allow = SUBAGENT_READ_NAMES | SUBAGENT_MUT_NAMES
    return [s for s in all_schemas if s.get("name") in allow]


def _execute_tool(tools: dict, tc: ToolCall) -> ToolResult:
    fn = tools.get(tc.name)
    if fn is None:
        return ToolResult.fail(display=f"subagent: unknown tool {tc.name}")
    try:
        args = tc.arguments if isinstance(tc.arguments, dict) else {}
        return fn(**args)
    except TypeError as e:
        return ToolResult.fail(display=f"subagent tool arg error ({tc.name}): {e}")
    except Exception as e:
        return ToolResult.fail(display=f"subagent tool failed ({tc.name}): {e}")


def run_subagent(
    adapter: BaseAdapter,
    tools: dict,
    schemas: list[dict],
    task: str,
    *,
    max_steps: int = SUBAGENT_MAX_STEPS,
) -> str:
    """Run an isolated tool loop; return final text only."""
    schemas = filter_schemas_for_subagent(schemas)
    tool_names = {s["name"] for s in schemas}
    tools = {k: v for k, v in tools.items() if k in tool_names}

    messages = [
        Message(role="system", content=SUBAGENT_SYSTEM),
        Message(role="user", content=task),
    ]
    last_text = ""
    for _ in range(max_steps):
        resp = adapter.send(messages, schemas)
        if not resp.tool_calls:
            last_text = (resp.content or "").strip()
            return structure_conclusion(last_text)
        messages.append(
            Message(
                role="assistant",
                content=resp.content,
                tool_calls=resp.tool_calls,
            )
        )
        for tc in resp.tool_calls:
            result = _execute_tool(tools, tc)
            messages.append(
                Message(
                    role="tool",
                    content=result.display or "",
                    tool_call_id=getattr(tc, "id", None),
                    name=tc.name,
                )
            )
        if resp.content:
            last_text = resp.content.strip()
    # Force a brief conclusion from last tool outcomes
    return structure_conclusion(
        last_text
        or "(subagent: reached max steps without final text; check partial tool results in logs)"
    )

"""Subagent — isolated tool-loop for exploration / focused edits.

Main agent calls spawn_subagent(task); the subagent runs with a reduced tool
surface and its own messages list. Only the final text conclusion is returned
to the parent context (no tool-call history).
"""
from __future__ import annotations

from typing import Any, Callable

from forge.adapters.base import BaseAdapter, Message, ToolCall, ToolResult

SUBAGENT_MAX_STEPS = 15

SUBAGENT_SYSTEM = """你是 Forge 子 Agent。完成主 Agent 交给你的子任务。
- 用工具探索与必要的小修改（str_replace / write_file）。
- 不要无限搜索；找到结论后用自然语言总结并停止调用工具。
- 最终回复必须是简洁结论（例如：bug 在 path 第 N 行，原因是…；或已修改哪些文件）。
- 不要把完整文件内容贴回最终结论。
"""

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
            return last_text or "(subagent: empty conclusion)"
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
    return (
        last_text
        or "(subagent: reached max steps without final text; check partial tool results in logs)"
    )

"""Shared OpenAI-compatible chat streaming → Message (provider parsing only)."""
from __future__ import annotations

import json
from typing import Any, Callable, Optional

from forge.adapters.base import Message, ToolCall

OnTextDelta = Optional[Callable[[str], None]]


def complete_chat_stream(
    client: Any,
    *,
    model: str,
    api_messages: list,
    api_tools: list | None,
    temperature: float = 0.1,
    on_text_delta: OnTextDelta = None,
) -> Message:
    """Run chat.completions.create(stream=True) and assemble a final Message.

    Text deltas are forwarded to on_text_delta as they arrive.
    Tool-call argument fragments are buffered until the stream ends; only then
    are they JSON-parsed into ToolCall objects (never executed here).
    """
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": api_messages,
        "temperature": temperature,
        "stream": True,
    }
    if api_tools:
        kwargs["tools"] = api_tools

    stream = client.chat.completions.create(**kwargs)
    content_parts: list[str] = []
    # index -> {id, name, arguments_str}
    tool_acc: dict[int, dict[str, str]] = {}

    for chunk in stream:
        if not getattr(chunk, "choices", None):
            continue
        choice = chunk.choices[0]
        delta = getattr(choice, "delta", None)
        if delta is None:
            continue
        piece = getattr(delta, "content", None)
        if piece:
            content_parts.append(piece)
            if on_text_delta is not None:
                try:
                    on_text_delta(piece)
                except Exception:
                    pass
        tcs = getattr(delta, "tool_calls", None)
        if not tcs:
            continue
        for tc in tcs:
            idx = getattr(tc, "index", 0) or 0
            slot = tool_acc.setdefault(
                idx, {"id": "", "name": "", "arguments": ""}
            )
            if getattr(tc, "id", None):
                slot["id"] = tc.id
            fn = getattr(tc, "function", None)
            if fn is None:
                continue
            if getattr(fn, "name", None):
                slot["name"] = fn.name
            arg = getattr(fn, "arguments", None)
            if arg:
                slot["arguments"] += arg

    content = "".join(content_parts) if content_parts else None
    tool_calls = None
    if tool_acc:
        tool_calls = []
        for idx in sorted(tool_acc.keys()):
            slot = tool_acc[idx]
            raw_args = slot.get("arguments") or ""
            try:
                args = json.loads(raw_args) if raw_args.strip() else {}
            except json.JSONDecodeError:
                return Message(
                    role="assistant",
                    content="工具参数格式错误，请重新生成合法 JSON 参数。",
                    tool_calls=None,
                )
            if not isinstance(args, dict):
                args = {}
            tool_calls.append(
                ToolCall(
                    id=slot.get("id") or f"call_{idx}",
                    name=slot.get("name") or "",
                    arguments=args,
                )
            )
    return Message(role="assistant", content=content, tool_calls=tool_calls)

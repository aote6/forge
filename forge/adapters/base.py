"""适配器基类 + 统一消息格式"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class ToolResult:
    """Tool outcome.

    success=False → FATAL (主操作未完成).
    success=True with payload["degraded"] → DEGRADED (主操作成功，关键状态不可信).
    success=True with payload["warnings"] → WARN (主操作与关键状态可信，附属失败).

    payload machine fields (only these severity keys):
      degraded: list[str]   e.g. ["path_map", "sync_watermark"]
      warnings: list[str]   e.g. ["cache_invalidate: ..."]
    Legacy: side_effect_warnings remains for migration; not the machine source of truth.
    SIDE_EFFECT_WARN in display is human-readable only.
    """
    success: bool
    payload: dict[str, Any] | None = None
    display: str = ""

    @classmethod
    def ok(cls, display: str, payload: dict = None) -> "ToolResult":
        return cls(success=True, payload=payload or {}, display=display)

    @classmethod
    def fail(cls, display: str, payload: dict = None) -> "ToolResult":
        return cls(success=False, payload=payload or {}, display=display)


@dataclass
class Message:
    role: str
    content: str | None = None
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None
    name: str | None = None
    raw_parts: list | None = None


class BaseAdapter(ABC):
    @abstractmethod
    def send(self, messages: list[Message], tools: list[dict]) -> Message:
        pass

    def send_stream(
        self,
        messages: list[Message],
        tools: list[dict],
        on_text_delta=None,
    ) -> Message:
        """Optional streaming path. Default: non-stream send() + one full-text delta.

        Subclasses that support provider streams should override and forward
        incremental text via on_text_delta(str). Tool-call fragments must be
        fully assembled before returning Message.tool_calls.
        """
        msg = self.send(messages, tools)
        if on_text_delta and msg.content:
            try:
                on_text_delta(msg.content)
            except Exception:
                pass
        return msg

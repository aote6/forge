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


class BaseAdapter(ABC):
    @abstractmethod
    def send(self, messages: list[Message], tools: list[dict]) -> Message:
        pass

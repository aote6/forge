"""会话管理 - 只管理单次会话的消息和摘要"""
from dataclasses import dataclass, field
from typing import Any
from forge.adapters.base import Message


@dataclass
class Conversation:
    history: list = field(default_factory=list)
    summary: str | None = None
    token_count: int = 0
    metadata: dict = field(default_factory=dict)
    
    def __init__(self, system_instruction: str = ""):
        self.history = []
        self.summary = None
        self.token_count = 0
        self.metadata = {}
        if system_instruction:
            self.history.append(Message(role="system", content=system_instruction))
    
    def append(self, msg: Message):
        self.history.append(msg)
    
    def get_messages(self) -> list:
        return self.history
    
    def clear_history(self):
        system_msgs = [m for m in self.history if m.role == "system"]
        self.history = system_msgs
        self.token_count = 0

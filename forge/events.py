"""事件系统"""
from enum import Enum
from dataclasses import dataclass, field
import time


class EventType(Enum):
    USER_MESSAGE = "user_message"
    TOOL_CALL_START = "tool_call_start"
    TOOL_CALL_END = "tool_call_end"
    ASSISTANT_REPLY = "assistant_reply"
    TRANSACTION_PREPARED = "transaction_prepared"
    TRANSACTION_COMMITTED = "transaction_committed"
    TRANSACTION_CANCELLED = "transaction_cancelled"
    ERROR = "error"


@dataclass
class Event:
    type: EventType
    data: dict = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    cancelled: bool = False
    
    def cancel(self):
        self.cancelled = True

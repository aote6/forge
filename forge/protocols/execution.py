"""执行持久化协议"""
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional

@dataclass
class TaskCheckpoint:
    task_id: str
    phase: str
    plan_id: Optional[str] = None
    completed_steps: List[str] = field(default_factory=list)
    state: Dict[str, Any] = field(default_factory=dict)

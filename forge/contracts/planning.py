"""规划协议"""
from dataclasses import dataclass, field
from typing import List

@dataclass
class PlanStep:
    step_id: str
    description: str
    target_files: List[str]
    operation_type: str
    dependencies: List[str] = field(default_factory=list)

@dataclass
class Plan:
    plan_id: str
    goal: str
    steps: List[PlanStep]
    assumptions: List[str] = field(default_factory=list)

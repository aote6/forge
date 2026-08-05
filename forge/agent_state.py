"""Agent 任务阶段状态机"""
from enum import Enum


class AgentPhase(Enum):
    IDLE = "idle"
    DISCOVERY = "discovery"
    ANALYSIS = "analysis"
    EDITING = "editing"
    WAIT_CONFIRM = "wait_confirm"
    VERIFYING = "verifying"
    REPORT = "report"
    DONE = "done"

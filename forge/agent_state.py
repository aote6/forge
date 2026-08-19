"""
DEPRECATED: DEPRECATED: phase machine for run_legacy only; production uses tool-loop.
Agent 任务阶段状态机"""
from enum import Enum


class AgentPhase(Enum):
    # v1 阶段
    IDLE = "idle"
    DISCOVERY = "discovery"
    ANALYSIS = "analysis"
    EDITING = "editing"
    WAIT_CONFIRM = "wait_confirm"
    VERIFYING = "verifying"
    REPORT = "report"
    DONE = "done"

    # v2 新增
    UNDERSTANDING = "understanding"
    PLANNING = "planning"
    CHECKING = "checking"
    EXECUTING = "executing"
    REVIEWING = "reviewing"

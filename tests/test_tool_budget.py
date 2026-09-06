"""Tests for the cross-turn tool-call budget (SUBAGENT_MAX_TOOL_CALLS)."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from forge.adapters.base import ToolCall, ToolResult
from forge.agent_abi import AgentTask, STATUS_NEED_DECISION
from forge.subagent import SUBAGENT_MAX_TOOL_CALLS, run_subagent


def test_tool_budget_preempts_at_limit(tmp_path: Path):
    """Model never says STOP_WHEN: met and keeps calling one tool per turn.

    Budget must cut it off exactly at SUBAGENT_MAX_TOOL_CALLS tool calls,
    without an extra adapter.send round beyond that.
    """
    sends = {"n": 0}
    calls = {"n": 0}

    def search_code(pattern, path="."):
        calls["n"] += 1
        return ToolResult.ok(display="hit")

    class Adapter:
        def send(self, messages, schemas):
            sends["n"] += 1
            return MagicMock(
                content="STOP_WHEN: not_met",
                tool_calls=[
                    ToolCall(
                        id=str(sends["n"]),
                        name="search_code",
                        arguments={"pattern": "x"},
                    )
                ],
            )

    result = run_subagent(
        Adapter(),
        {"search_code": search_code},
        [{"name": "search_code", "parameters": {}}],
        AgentTask(goal="t", max_steps=100),
        project_root=tmp_path,
    )

    assert sends["n"] == SUBAGENT_MAX_TOOL_CALLS
    assert calls["n"] == SUBAGENT_MAX_TOOL_CALLS
    assert result.status == STATUS_NEED_DECISION
    assert result.status_reason.startswith("preempted_tool_budget")

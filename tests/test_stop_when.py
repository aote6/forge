"""Tests for STOP_WHEN hard stop in run_subagent loop."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from forge.adapters.base import ToolCall, ToolResult
from forge.agent_abi import AgentTask, STATUS_NEED_DECISION
from forge.subagent import (
    parse_stop_when,
    run_subagent,
    strip_stop_when,
)


def test_parse_stop_when_met_and_not_met():
    assert parse_stop_when("hello\nSTOP_WHEN: met\n") == "met"
    assert parse_stop_when("STOP_WHEN: not_met") == "not_met"
    assert parse_stop_when("no signal here") == "not_met"
    assert parse_stop_when("") == "not_met"
    # last wins
    assert parse_stop_when("STOP_WHEN: not_met\nSTOP_WHEN: met\n") == "met"


def test_strip_stop_when_removes_control_line():
    text = "STOP_WHEN: met\nCONCLUSION:\nhello\n"
    out = strip_stop_when(text)
    assert "STOP_WHEN" not in out
    assert "CONCLUSION" in out


def test_stop_when_met_discards_tool_calls_zero_invocations(tmp_path: Path):
    calls = {"n": 0}

    def search_code(pattern, path="."):
        calls["n"] += 1
        return ToolResult.ok(display="hit")

    class Adapter:
        def send(self, messages, schemas):
            return MagicMock(
                content="STOP_WHEN: met\nCONCLUSION:\ndone without tools\n",
                tool_calls=[
                    ToolCall(id="1", name="search_code", arguments={"pattern": "x"})
                ],
            )

    tools = {"search_code": search_code}
    schemas = [{"name": "search_code", "parameters": {}}]
    out = run_subagent(
        Adapter(),
        tools,
        schemas,
        AgentTask(goal="t", max_steps=5),
        project_root=tmp_path,
    )
    assert calls["n"] == 0
    assert out.stop_when_met is True


def test_stop_when_met_returns_without_next_round(tmp_path: Path):
    sends = {"n": 0}

    class Adapter:
        def send(self, messages, schemas):
            sends["n"] += 1
            return MagicMock(
                content="STOP_WHEN: met\nCONCLUSION:\nonly once\n",
                tool_calls=None,
            )

    out = run_subagent(
        Adapter(),
        {},
        [],
        AgentTask(goal="t", max_steps=10),
        project_root=tmp_path,
    )
    assert sends["n"] == 1
    assert out.stop_when_met is True


def test_not_met_with_tool_calls_continues(tmp_path: Path):
    calls = {"n": 0}
    sends = {"n": 0}

    def search_code(pattern, path="."):
        calls["n"] += 1
        return ToolResult.ok(display="hit")

    class Adapter:
        def send(self, messages, schemas):
            sends["n"] += 1
            if sends["n"] == 1:
                return MagicMock(
                    content="STOP_WHEN: not_met",
                    tool_calls=[
                        ToolCall(id="1", name="search_code", arguments={"pattern": "x"})
                    ],
                )
            return MagicMock(
                content="STOP_WHEN: not_met\nCONCLUSION:\nafter tool\n",
                tool_calls=None,
            )

    out = run_subagent(
        Adapter(),
        {"search_code": search_code},
        [{"name": "search_code", "parameters": {}}],
        AgentTask(goal="t", max_steps=5),
        project_root=tmp_path,
    )
    assert calls["n"] == 1
    assert sends["n"] == 2
    assert out.stop_when_met is False
    assert out.status == STATUS_NEED_DECISION


def test_missing_signal_treated_as_not_met(tmp_path: Path):
    class Adapter:
        def send(self, messages, schemas):
            return MagicMock(content="CONCLUSION:\nfree form\n", tool_calls=None)

    out = run_subagent(
        Adapter(),
        {},
        [],
        AgentTask(goal="t", max_steps=3),
        project_root=tmp_path,
    )
    assert out.stop_when_met is False

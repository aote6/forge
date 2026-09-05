"""Subagent tool-boundary preempt (constraint / tool-fail / budget)."""

from __future__ import annotations

from unittest.mock import MagicMock

from forge.adapters.base import ToolCall, ToolResult
from forge.agent_abi import STATUS_DONE, STATUS_NEED_DECISION, AgentResult, AgentTask
from forge.subagent import filter_schemas_for_subagent, run_subagent
from forge.tools.schemas import MUTATION_TOOL_DECLARATIONS, READ_ONLY_TOOL_DECLARATIONS

_SUB_SCHEMAS = filter_schemas_for_subagent(
    list(READ_ONLY_TOOL_DECLARATIONS) + list(MUTATION_TOOL_DECLARATIONS)
)


def _tc(name="search_code", arguments=None, id_="1"):
    return ToolCall(id=id_, name=name, arguments=arguments or {"pattern": "x"})


def test_preempt_constraint_deny_ge2():
    """约束违规 ≥2 → need_decision + preempted_constraint."""

    class FakeAdapter:
        def __init__(self):
            self.n = 0

        def send(self, messages, schemas):
            self.n += 1
            # Every turn requests a blacklisted tool → enforce deny
            return MagicMock(
                content="STOP_WHEN: not_met",
                tool_calls=[_tc(name="search_code", id_=str(self.n))],
            )

    tools = {
        "search_code": lambda pattern, path=".": ToolResult.ok(display="hit"),
    }
    out = run_subagent(
        FakeAdapter(),
        tools,
        _SUB_SCHEMAS,
        AgentTask(
            goal="find",
            max_steps=10,
            constraints={"not_allowed": ["search_code"]},
        ),
    )
    assert isinstance(out, AgentResult)
    assert out.status == STATUS_NEED_DECISION
    assert "preempted_constraint" in out.status_reason


def test_preempt_consecutive_tool_fail_ge3():
    """连续工具失败 ≥3 → need_decision + preempted_tool_fail."""
    calls = {"n": 0}

    class FakeAdapter:
        def __init__(self):
            self.n = 0

        def send(self, messages, schemas):
            self.n += 1
            return MagicMock(
                content="STOP_WHEN: not_met",
                tool_calls=[_tc(id_=str(self.n))],
            )

    def always_fail(pattern, path="."):
        calls["n"] += 1
        return ToolResult.fail(display="boom")

    out = run_subagent(
        FakeAdapter(),
        {"search_code": always_fail},
        _SUB_SCHEMAS,
        AgentTask(goal="find", max_steps=10),
    )
    assert out.status == STATUS_NEED_DECISION
    assert "preempted_tool_fail" in out.status_reason
    # Tool fully executed 3 times before preempt (never mid-tool)
    assert calls["n"] == 3


def test_preempt_budget_ge_70pct_without_met():
    """步数 ≥70% 且未 met → need_decision + preempted_budget."""

    class FakeAdapter:
        def __init__(self):
            self.n = 0

        def send(self, messages, schemas):
            self.n += 1
            return MagicMock(
                content="STOP_WHEN: not_met",
                tool_calls=[_tc(id_=str(self.n))],
            )

    tools = {
        "search_code": lambda pattern, path=".": ToolResult.ok(display="ok"),
    }
    # max_steps=10 → budget_limit = ceil(7) = 7
    out = run_subagent(
        FakeAdapter(),
        tools,
        _SUB_SCHEMAS,
        AgentTask(goal="find", max_steps=10),
    )
    assert out.status == STATUS_NEED_DECISION
    assert "preempted_budget" in out.status_reason


def test_budget_not_preempt_when_stop_when_met():
    """步数将达 70% 但 STOP_WHEN: met → 正常收敛，不 preempted_budget。"""

    class FakeAdapter:
        def __init__(self):
            self.n = 0

        def send(self, messages, schemas):
            self.n += 1
            if self.n < 7:
                return MagicMock(
                    content="STOP_WHEN: not_met",
                    tool_calls=[_tc(id_=str(self.n))],
                )
            # met before/at budget boundary — discard tools, finalize stop_when
            return MagicMock(
                content=(
                    "STOP_WHEN: met\n"
                    "CONCLUSION:\ndone\n"
                    "EVIDENCE:\n- tool_call_id=1 path=a.py hit\n"
                    "UNCERTAIN:\n(无)\n"
                    "NEXT:\n(无)\n"
                ),
                tool_calls=[_tc(id_="final")],
            )

    tools = {
        "search_code": lambda pattern, path=".": ToolResult.ok(display="ok"),
    }
    out = run_subagent(
        FakeAdapter(),
        tools,
        _SUB_SCHEMAS,
        AgentTask(goal="find", max_steps=10),
    )
    # met path → done or blocked (evidence rules), never preempted_budget
    assert "preempted_budget" not in (out.status_reason or "")
    assert out.status in {STATUS_DONE, "blocked", STATUS_NEED_DECISION}
    if out.status == STATUS_NEED_DECISION:
        assert "preempted_" not in out.status_reason


def test_preempt_never_interrupts_mid_tool():
    """抢占只在工具返回之后：每次失败工具都完整执行完毕。"""
    order = []

    class FakeAdapter:
        def __init__(self):
            self.n = 0

        def send(self, messages, schemas):
            self.n += 1
            return MagicMock(
                content="STOP_WHEN: not_met",
                tool_calls=[_tc(id_=str(self.n))],
            )

    def tracked_fail(pattern, path="."):
        order.append("enter")
        order.append("exit")
        return ToolResult.fail(display="fail")

    out = run_subagent(
        FakeAdapter(),
        {"search_code": tracked_fail},
        _SUB_SCHEMAS,
        AgentTask(goal="find", max_steps=10),
    )
    assert out.status == STATUS_NEED_DECISION
    assert "preempted_tool_fail" in out.status_reason
    assert order == ["enter", "exit"] * 3

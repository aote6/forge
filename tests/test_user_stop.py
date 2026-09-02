"""P1 soft-stop (Ctrl+C / stop_requested) — cooperative boundary exit."""
from __future__ import annotations

from types import SimpleNamespace

from forge.agent_abi import AgentTask, STATUS_BLOCKED, CandidateResult, assemble_agent_result
from forge.adapters.base import ToolResult
from forge.subagent import run_subagent
from forge.tools.schemas import EXECUTION_PLANE_TOOL_DECLARATIONS


class _ScriptAdapter:
    def __init__(self, responses):
        self.responses = list(responses)
        self.send_calls = 0

    def send(self, messages, schemas):
        self.send_calls += 1
        if not self.responses:
            return SimpleNamespace(content="done", tool_calls=[])
        return self.responses.pop(0)


def _tc(name: str, args: dict | None = None, id_: str = "c1"):
    return SimpleNamespace(name=name, arguments=args or {}, id=id_)


def _resp(content: str, tool_calls):
    return SimpleNamespace(content=content, tool_calls=tool_calls)


def test_subagent_should_stop_before_next_send(tmp_path):
    calls = {"n": 0}
    stop = {"v": False}

    def counting_tool(**kwargs):
        calls["n"] += 1
        stop["v"] = True
        return ToolResult.ok(display="file-body")

    tools = {"read_file": counting_tool}
    adapter = _ScriptAdapter(
        [
            _resp("step1", [_tc("read_file", {"path": "a.py"})]),
            _resp("step2", [_tc("read_file", {"path": "b.py"})]),
        ]
    )
    task = AgentTask(goal="read", subtask_id="s1", max_steps=5)
    result = run_subagent(
        adapter,
        tools,
        list(EXECUTION_PLANE_TOOL_DECLARATIONS),
        task,
        project_root=str(tmp_path),
        should_stop=lambda: stop["v"],
    )
    assert result.status == STATUS_BLOCKED
    assert "user_stop" in result.status_reason
    assert adapter.send_calls == 1
    assert calls["n"] == 1


def test_subagent_keyboard_interrupt_on_send(tmp_path):
    class BoomAdapter:
        def send(self, messages, schemas):
            raise KeyboardInterrupt

    task = AgentTask(goal="x", subtask_id="s2", max_steps=3)
    result = run_subagent(
        BoomAdapter(),
        {},
        list(EXECUTION_PLANE_TOOL_DECLARATIONS),
        task,
        project_root=str(tmp_path),
    )
    assert result.status == STATUS_BLOCKED
    assert "user_stop" in result.status_reason


def test_confirm_keyboard_interrupt_is_user_stop_not_denied(tmp_path):
    adapter = _ScriptAdapter(
        [
            _resp(
                "write",
                [_tc("str_replace", {"path": "a.py", "old_string": "x", "new_string": "y"})],
            )
        ]
    )

    def tools_str_replace(**kwargs):
        return ToolResult.ok(display="written")

    task = AgentTask(goal="edit", subtask_id="s3", max_steps=3)
    result = run_subagent(
        adapter,
        {"str_replace": tools_str_replace},
        list(EXECUTION_PLANE_TOOL_DECLARATIONS),
        task,
        project_root=str(tmp_path),
        confirm_fn=lambda s: (_ for _ in ()).throw(KeyboardInterrupt()),
    )
    assert "user_stop" in result.status_reason
    assert "user_denied_write" not in result.status_reason


def test_user_denied_write_still_distinct(tmp_path):
    adapter = _ScriptAdapter(
        [
            _resp(
                "write",
                [_tc("str_replace", {"path": "a.py", "old_string": "x", "new_string": "y"})],
            )
        ]
    )

    def tools_str_replace(**kwargs):
        return ToolResult.ok(display="written")

    task = AgentTask(goal="edit", subtask_id="s4", max_steps=3)
    result = run_subagent(
        adapter,
        {"str_replace": tools_str_replace},
        list(EXECUTION_PLANE_TOOL_DECLARATIONS),
        task,
        project_root=str(tmp_path),
        confirm_fn=lambda s: False,
    )
    assert "user_denied_write" in result.status_reason


def test_stop_when_met_not_confused_with_user_stop(tmp_path):
    adapter = _ScriptAdapter([_resp("STOP_WHEN: met\nCONCLUSION:\nok\nEVIDENCE:\n-\nUNCERTAIN:\n-\nNEXT:\n-", [])])
    task = AgentTask(goal="g", subtask_id="s5", max_steps=3, stop_when="done")
    result = run_subagent(
        adapter,
        {},
        list(EXECUTION_PLANE_TOOL_DECLARATIONS),
        task,
        project_root=str(tmp_path),
    )
    assert "user_stop" not in result.status_reason
    # stop_when without evidence → blocked with stop_when messaging, or done path
    assert result.stop_when_met is True


def test_assemble_user_stop_status():
    cand = CandidateResult(
        conclusion="stopped",
        evidence_items=[],
        stop_when_met=False,
        exit_kind="user_stop",
        error_message="user_stop",
    )
    task = AgentTask(goal="g", subtask_id="s")
    ar = assemble_agent_result(task, cand, records=[], subtask_id="s")
    assert ar.status == STATUS_BLOCKED
    assert "user_stop" in ar.status_reason


def test_runtime_run_swallows_keyboardinterrupt(tmp_path):
    from forge.runtime import Runtime
    from forge.workspace import Workspace
    from forge.memory import MemoryStore

    class A:
        model_name = "t"

        def send(self, messages, schemas):
            raise KeyboardInterrupt

    ws = Workspace(project_root=str(tmp_path))
    rt = Runtime(A(), ws, MemoryStore())
    out = rt.run("hello")
    assert "已停止当前任务" in out
    assert rt.stop_requested() is True

    class A2:
        model_name = "t"

        def send(self, messages, schemas):
            return SimpleNamespace(content="ok reply", tool_calls=[])

    rt2 = Runtime(A2(), ws, MemoryStore())
    out2 = rt2.run("next")
    assert "已停止当前任务" not in (out2 or "")
    assert "ok reply" in (out2 or "")

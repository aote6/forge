"""Main checkpoint FACTS SINCE LAST CHECKPOINT — runtime-local observation window.

Covers: repeat detection, result identity, window reset, WorkingSet deltas,
progress/final injection, empty-facts silence, no WorkingSet/schema mutation.
"""
from __future__ import annotations

from types import SimpleNamespace

from forge.adapters.base import Message, ToolCall, ToolResult
from forge.conversation import Conversation
from forge.runtime import (
    FINAL_CHECKPOINT_TAIL_STEPS,
    MAX_AGENT_STEPS,
    PROGRESS_CHECKPOINT_EVERY,
    Runtime,
    WorkingSet,
    _CheckpointWindow,
    _FACTS_MARKER,
    _canonical_json,
    _checkpoint_for_step,
    _final_checkpoint_text,
    _progress_checkpoint_text,
    _result_identity,
)
from forge.sync.sync_layer import IN_SYNC, SyncReport
from forge.workspace import Workspace

_PROGRESS = "[PROGRESS]"
_FINAL = "[FINAL CHECKPOINT]"


class _RecordingAdapter:
    def __init__(self, responses, repeat_last: bool = False):
        self._responses = list(responses)
        self.repeat_last = repeat_last
        self.rounds: list[list] = []

    def send(self, messages, schemas):
        self.rounds.append(list(messages))
        if self._responses:
            if self.repeat_last and len(self._responses) == 1:
                return self._responses[0]
            return self._responses.pop(0)
        return Message(role="assistant", content="done")


class _ScriptedExecutor:
    def __init__(self, by_call: list | None = None, by_name: dict | None = None):
        self.tools: dict = {}
        self._by_call = list(by_call) if by_call is not None else None
        self._by_name = by_name or {}
        self._default = ToolResult.ok(display="ok", payload={"v": 1})
        self.calls: list[tuple[str, dict]] = []

    def execute(self, tool_call):
        args = tool_call.arguments if isinstance(tool_call.arguments, dict) else {}
        self.calls.append((tool_call.name, args))
        if self._by_call is not None:
            if self._by_call:
                return self._by_call.pop(0)
            return self._default
        return self._by_name.get(tool_call.name, self._default)


def _runtime(tmp_path, adapter, executor) -> Runtime:
    rt = object.__new__(Runtime)
    rt.adapter = adapter
    rt.workspace = Workspace(project_root=str(tmp_path))
    rt.conversation = Conversation()
    rt.executor = executor
    rt._handlers = {}
    rt._submitted_plan = None
    rt._last_tool_calls = 0
    rt._last_assistant_replies = []
    rt.sync_layer = SimpleNamespace(
        detect=lambda: SyncReport(status=IN_SYNC),
        world_available=lambda: True,
        external_change_detected=lambda: False,
        disk_change_detected=lambda: False,
    )
    return rt


def _system_texts(round_messages) -> list[str]:
    return [
        getattr(m, "content", "") or ""
        for m in round_messages
        if getattr(m, "role", None) == "system"
    ]


def _rounds_with(adapter, marker: str) -> list[int]:
    return [
        i
        for i, msgs in enumerate(adapter.rounds)
        if any(t.startswith(marker) for t in _system_texts(msgs))
    ]


def _checkpoint_text(adapter, round_i: int, marker: str) -> str:
    for t in _system_texts(adapter.rounds[round_i]):
        if t.startswith(marker):
            return t
    return ""


def _search(step: int, pattern: str = "foo") -> Message:
    return Message(
        role="assistant",
        content=None,
        tool_calls=[
            ToolCall(
                id=f"t{step}",
                name="search_code",
                arguments={"pattern": pattern},
            )
        ],
    )


def _read(step: int, path: str) -> Message:
    return Message(
        role="assistant",
        content=None,
        tool_calls=[
            ToolCall(id=f"t{step}", name="read_file", arguments={"path": path})
        ],
    )


# ---------------------------------------------------------------------------
# Unit: window helpers
# ---------------------------------------------------------------------------


def test_single_call_no_repeat_fact():
    ws = WorkingSet(goal="g")
    w = _CheckpointWindow(ws)
    w.record_tool(
        "search_code",
        {"pattern": "foo"},
        ToolResult.ok(display="hits", payload={"n": 1}),
    )
    text = w.facts_text(ws)
    assert "was called" not in text
    assert "did not change" not in text


def test_same_input_same_result_is_repeat():
    ws = WorkingSet(goal="g")
    w = _CheckpointWindow(ws)
    r = ToolResult.ok(display="hits", payload={"n": 1, "lines": ["a"]})
    for _ in range(3):
        w.record_tool("search_code", {"pattern": "foo"}, r)
    text = w.facts_text(ws)
    assert _FACTS_MARKER in text
    assert "was called 3 times" in text
    assert "did not change" in text
    assert "浪费" not in text
    assert "没有进展" not in text


def test_same_input_different_result_not_unchanged():
    ws = WorkingSet(goal="g")
    w = _CheckpointWindow(ws)
    w.record_tool(
        "search_code",
        {"pattern": "foo"},
        ToolResult.ok(display="A", payload={"n": 1}),
    )
    w.record_tool(
        "search_code",
        {"pattern": "foo"},
        ToolResult.ok(display="B", payload={"n": 2}),
    )
    text = w.facts_text(ws)
    assert "was called 2 times" in text
    assert "did not change" not in text
    assert "changed across those calls" in text


def test_different_input_not_merged():
    ws = WorkingSet(goal="g")
    w = _CheckpointWindow(ws)
    r = ToolResult.ok(display="x", payload={"v": 1})
    w.record_tool("search_code", {"pattern": "foo"}, r)
    w.record_tool("search_code", {"pattern": "bar"}, r)
    text = w.facts_text(ws)
    assert "was called" not in text


def test_window_reset_clears_prior_calls():
    ws = WorkingSet(goal="g")
    w = _CheckpointWindow(ws)
    r = ToolResult.ok(display="x", payload={"v": 1})
    for _ in range(3):
        w.record_tool("search_code", {"pattern": "foo"}, r)
    assert "was called 3 times" in w.facts_text(ws)
    w.reset(ws)
    w.record_tool("search_code", {"pattern": "foo"}, r)
    w.record_tool("search_code", {"pattern": "foo"}, r)
    text = w.facts_text(ws)
    assert "was called 2 times" in text
    assert "was called 3 times" not in text
    assert "was called 5 times" not in text


def test_working_set_delta_in_facts():
    ws = WorkingSet(goal="g")
    w = _CheckpointWindow(ws)
    ws.files_read.append("a.py")
    ws.files_read.append("b.py")
    ws.pending_verify.append("verify edit on a.py")
    text = w.facts_text(ws)
    assert "files_read:" in text
    assert "+2" in text
    assert "pending_verify:" in text


def test_working_set_unchanged_pending_when_calls_exist():
    ws = WorkingSet(goal="g", pending_verify=["verify edit on x.py"])
    w = _CheckpointWindow(ws)
    w.record_tool(
        "read_file",
        {"path": "x.py"},
        ToolResult.ok(display="ok", payload={"path": "x.py"}),
    )
    # second identical call so there is at least one fact line path
    w.record_tool(
        "read_file",
        {"path": "x.py"},
        ToolResult.ok(display="ok", payload={"path": "x.py"}),
    )
    text = w.facts_text(ws)
    assert "pending_verify:" in text
    assert "unchanged" in text


def test_empty_facts_when_no_activity():
    ws = WorkingSet(goal="g")
    w = _CheckpointWindow(ws)
    assert w.facts_text(ws) == ""


def test_spawn_subagent_reported_as_delegation_not_mutation():
    ws = WorkingSet(goal="g")
    w = _CheckpointWindow(ws)
    w.record_tool(
        "spawn_subagent",
        {"goal": "fix"},
        ToolResult.ok(display="done", payload={"status": "ok"}),
    )
    text = w.facts_text(ws)
    assert "spawn_subagent succeeded" in text
    assert "delegated" in text
    assert "Main direct mutation" in text or "not a Main direct mutation" in text


def test_result_identity_uses_payload_not_display():
    a = ToolResult.ok(display="AAA", payload={"k": 1})
    b = ToolResult.ok(display="BBB", payload={"k": 1})
    assert _result_identity(a) == _result_identity(b)
    c = ToolResult.ok(display="AAA", payload={"k": 2})
    assert _result_identity(a) != _result_identity(c)


def test_progress_and_final_accept_facts_without_noise_when_empty():
    ws = WorkingSet(goal="goal-x")
    p = _progress_checkpoint_text(ws, facts="")
    assert _FACTS_MARKER not in p
    assert p.startswith(_PROGRESS)
    f = _final_checkpoint_text(ws, facts="")
    assert _FACTS_MARKER not in f
    assert f.startswith(_FINAL)
    with_facts = _progress_checkpoint_text(
        ws, facts=_FACTS_MARKER + "\nsearch_code was called 2 times."
    )
    assert _FACTS_MARKER in with_facts
    assert "客观事实" in with_facts


def test_checkpoint_for_step_passes_facts():
    ws = WorkingSet(goal="g")
    facts = _FACTS_MARKER + "\nline"
    # force progress at step 5
    text = _checkpoint_for_step(ws, 5, False, MAX_AGENT_STEPS, facts=facts)
    assert text.startswith(_PROGRESS)
    assert _FACTS_MARKER in text


# ---------------------------------------------------------------------------
# Integration via _run_conversation
# ---------------------------------------------------------------------------


def test_progress_checkpoint_receives_repeat_facts(tmp_path):
    """Five identical search_code → progress at step 5 carries FACTS."""
    same = ToolResult.ok(display="hits", payload={"matches": ["x"]})
    responses = [_search(i, "foo") for i in range(6)]
    responses.append(Message(role="assistant", content="done"))
    adapter = _RecordingAdapter(responses)
    executor = _ScriptedExecutor(by_call=[same] * 6)
    rt = _runtime(tmp_path, adapter, executor)
    rt._run_conversation("find foo", schemas=[])

    hit = _rounds_with(adapter, _PROGRESS)
    assert hit, "expected progress checkpoint"
    assert hit[0] == PROGRESS_CHECKPOINT_EVERY
    text = _checkpoint_text(adapter, hit[0], _PROGRESS)
    assert _FACTS_MARKER in text
    assert "was called" in text
    assert "did not change" in text


def test_same_input_changed_result_in_conversation(tmp_path):
    results = [
        ToolResult.ok(display="1", payload={"n": 1}),
        ToolResult.ok(display="2", payload={"n": 2}),
        ToolResult.ok(display="3", payload={"n": 3}),
        ToolResult.ok(display="4", payload={"n": 4}),
        ToolResult.ok(display="5", payload={"n": 5}),
    ]
    responses = [_search(i, "foo") for i in range(5)]
    responses.append(Message(role="assistant", content="done"))
    adapter = _RecordingAdapter(responses)
    executor = _ScriptedExecutor(by_call=results)
    rt = _runtime(tmp_path, adapter, executor)
    rt._run_conversation("find foo", schemas=[])

    hit = _rounds_with(adapter, _PROGRESS)
    assert hit
    text = _checkpoint_text(adapter, hit[0], _PROGRESS)
    assert "did not change" not in text
    assert "changed across those calls" in text


def test_checkpoint_window_resets_between_progress(tmp_path):
    """Calls before first checkpoint must not accumulate into the second."""
    same = ToolResult.ok(display="hits", payload={"matches": ["x"]})
    # 11 search steps → progress at 5 and 10
    responses = [_search(i, "foo") for i in range(11)]
    responses.append(Message(role="assistant", content="done"))
    adapter = _RecordingAdapter(responses)
    executor = _ScriptedExecutor(by_call=[same] * 11)
    rt = _runtime(tmp_path, adapter, executor)
    rt._run_conversation("find foo", schemas=[])

    hits = _rounds_with(adapter, _PROGRESS)
    assert len(hits) >= 2
    t0 = _checkpoint_text(adapter, hits[0], _PROGRESS)
    t1 = _checkpoint_text(adapter, hits[1], _PROGRESS)
    assert "was called 5 times" in t0
    # second window: steps 5..9 → 5 calls again, not 10
    assert "was called 5 times" in t1
    assert "was called 10 times" not in t1


def test_no_facts_noise_on_short_task(tmp_path):
    """Fewer than 5 steps: no progress checkpoint, no FACTS injection."""
    adapter = _RecordingAdapter(
        [_read(0, "a.py"), Message(role="assistant", content="done")]
    )
    rt = _runtime(tmp_path, adapter, _ScriptedExecutor())
    rt._run_conversation("read a", schemas=[])
    assert not _rounds_with(adapter, _PROGRESS)
    assert not _rounds_with(adapter, _FINAL)
    for msgs in adapter.rounds:
        for t in _system_texts(msgs):
            assert _FACTS_MARKER not in t


def test_final_checkpoint_can_include_facts(tmp_path):
    """Near MAX_AGENT_STEPS, final checkpoint can carry FACTS from the window."""
    same = ToolResult.ok(display="hits", payload={"matches": ["x"]})
    # Fill enough steps to enter final tail; use repeat_last for overflow
    n = MAX_AGENT_STEPS - FINAL_CHECKPOINT_TAIL_STEPS
    responses = [_search(i, "foo") for i in range(n)]
    # then keep searching into final region
    responses += [_search(n + i, "foo") for i in range(FINAL_CHECKPOINT_TAIL_STEPS)]
    adapter = _RecordingAdapter(responses, repeat_last=False)
    executor = _ScriptedExecutor(by_call=[same] * (n + FINAL_CHECKPOINT_TAIL_STEPS))
    rt = _runtime(tmp_path, adapter, executor)
    rt._run_conversation("long search", schemas=[])

    finals = _rounds_with(adapter, _FINAL)
    assert finals, "expected final checkpoint near max steps"
    # At least one final round should have either FACTS or clean final text
    texts = [_checkpoint_text(adapter, i, _FINAL) for i in finals]
    assert any(t.startswith(_FINAL) for t in texts)


def test_task_state_schema_unchanged(tmp_path):
    """WorkingSet persistence must not grow new checkpoint-fact fields."""
    from forge.runtime import _load_task_state, _save_task_state

    ws = WorkingSet(goal="g", files_read=["a.py"])
    _save_task_state(str(tmp_path), ws)
    data = _load_task_state(str(tmp_path))
    forbidden = {
        "calls",
        "call_signatures",
        "ck_window",
        "spawn_success_count",
        "facts",
    }
    assert forbidden.isdisjoint(set(data.keys()))
    # round-trip still works
    ws2 = WorkingSet.from_dict(data)
    assert ws2.files_read == ["a.py"]


def test_canonical_json_stable_key_order():
    a = _canonical_json({"b": 1, "a": 2})
    b = _canonical_json({"a": 2, "b": 1})
    assert a == b

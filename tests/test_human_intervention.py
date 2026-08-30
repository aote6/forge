"""HUMAN_INTERVENTION_CONTRACT v1 — core closed loop tests."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from forge.adapters.base import ToolCall, ToolResult
from forge.runtime_state import (
    PHASE_ABORTED,
    PHASE_AWAITING_USER,
    PHASE_IDLE,
    PENDING_KIND_HUMAN_INTERVENTION,
    PENDING_KIND_SYNC_DECISION,
    RECOVERY_DECISION_REQUIRED,
    Pending,
    RuntimeState,
    RuntimeStateStore,
    derive_recovery,
    human_intervention_pending_blocks,
)


def test_human_pending_roundtrip(tmp_path: Path):
    store = RuntimeStateStore(tmp_path)
    st = RuntimeState(
        phase=PHASE_AWAITING_USER,
        pending=Pending(
            kind=PENDING_KIND_HUMAN_INTERVENTION,
            summary="human_intervention: need choice",
            payload={
                "reason": "need choice",
                "options_context": "ctx",
                "proposed_next": "try A",
                "requested_at": 1.0,
            },
        ),
    )
    store.save(st)
    loaded = store.load()
    assert loaded.phase == PHASE_AWAITING_USER
    assert loaded.pending is not None
    assert loaded.pending.kind == PENDING_KIND_HUMAN_INTERVENTION
    assert loaded.pending.payload["reason"] == "need choice"
    assert loaded.pending.payload["options_context"] == "ctx"
    assert loaded.recovery.mode == RECOVERY_DECISION_REQUIRED


def test_derive_recovery_human_intervention():
    p = Pending(kind=PENDING_KIND_HUMAN_INTERVENTION, summary="x", payload={"reason": "r"})
    rec = derive_recovery(PHASE_AWAITING_USER, p)
    assert rec.mode == RECOVERY_DECISION_REQUIRED
    assert "human_intervention" in (rec.reason or "")


def test_sync_decision_still_roundtrips(tmp_path: Path):
    store = RuntimeStateStore(tmp_path)
    st = RuntimeState(
        phase=PHASE_AWAITING_USER,
        pending=Pending(
            kind=PENDING_KIND_SYNC_DECISION,
            summary="sync",
            payload={"decision_id": "d1", "basis": "CONFLICT"},
        ),
    )
    store.save(st)
    loaded = store.load()
    assert loaded.pending.kind == PENDING_KIND_SYNC_DECISION
    assert loaded.recovery.mode == RECOVERY_DECISION_REQUIRED


def _minimal_runtime(tmp_path: Path):
    from forge.conversation import Conversation
    from forge.events import EventType
    from forge.runtime import Runtime
    from forge.workspace import Workspace

    rt = object.__new__(Runtime)
    rt.workspace = Workspace(project_root=str(tmp_path))
    rt.conversation = Conversation()
    rt._handlers = {t: [] for t in EventType}
    rt._pending_action = None
    rt._working_set = None
    rt._last_tool_calls = 0
    rt._last_assistant_replies = []
    rt._last_response_needs_display = False
    rt._runtime_state_store = RuntimeStateStore(tmp_path)
    rt.runtime_state = rt._runtime_state_store.load()
    rt.recovery = rt.runtime_state.recovery
    rt.sync_layer = None
    rt.executor = SimpleNamespace(tools={}, execute=lambda tc: ToolResult.fail(display="no"))
    return rt


def test_request_persists_and_sets_state(tmp_path: Path):
    rt = _minimal_runtime(tmp_path)
    res = rt.request_human_intervention(reason="cannot continue", options_context="ctx")
    assert res.success
    assert rt.runtime_state.phase == PHASE_AWAITING_USER
    assert rt.runtime_state.pending.kind == PENDING_KIND_HUMAN_INTERVENTION
    loaded = RuntimeStateStore(tmp_path).load()
    assert loaded.pending.kind == PENDING_KIND_HUMAN_INTERVENTION
    assert loaded.recovery.mode == RECOVERY_DECISION_REQUIRED
    assert "Needs human decision" in (res.display or "")


def test_request_refuses_when_pending_action(tmp_path: Path):
    from forge.runtime import PendingAction

    rt = _minimal_runtime(tmp_path)
    rt._pending_action = PendingAction(tool="str_replace", args={}, tool_call_id="x")
    res = rt.request_human_intervention(reason="x")
    assert not res.success
    assert rt.runtime_state.pending is None


def test_request_refuses_when_sync_pending(tmp_path: Path):
    rt = _minimal_runtime(tmp_path)
    rt.runtime_state.phase = PHASE_AWAITING_USER
    rt.runtime_state.pending = Pending(
        kind=PENDING_KIND_SYNC_DECISION, summary="s", payload={}
    )
    # phase not IDLE → refuse
    res = rt.request_human_intervention(reason="x")
    assert not res.success


def test_request_mutual_exclusion_idle_with_pending(tmp_path: Path):
    rt = _minimal_runtime(tmp_path)
    rt.runtime_state.pending = Pending(
        kind=PENDING_KIND_SYNC_DECISION, summary="s", payload={}
    )
    # force phase IDLE but pending set
    rt.runtime_state.phase = PHASE_IDLE
    res = rt.request_human_intervention(reason="x")
    assert not res.success
    assert "pending" in (res.display or "").lower() or "kind=" in (res.display or "")


def test_resolve_continue_modify_abort(tmp_path: Path):
    rt = _minimal_runtime(tmp_path)
    assert rt.request_human_intervention(reason="r").success

    r1 = rt.resolve_human_intervention(decision="continue")
    assert r1.success
    assert rt.runtime_state.phase == PHASE_IDLE
    assert rt.runtime_state.pending is None

    assert rt.request_human_intervention(reason="r2").success
    r2 = rt.resolve_human_intervention(decision="modify", user_note="")
    assert not r2.success

    r2b = rt.resolve_human_intervention(decision="modify", user_note="do B")
    assert r2b.success
    assert rt.runtime_state.phase == PHASE_IDLE

    assert rt.request_human_intervention(reason="r3").success
    r3 = rt.resolve_human_intervention(decision="abort")
    assert r3.success
    assert rt.runtime_state.phase == PHASE_ABORTED
    assert rt.runtime_state.pending is None


def test_aborted_to_idle_on_new_task(tmp_path: Path):
    rt = _minimal_runtime(tmp_path)
    rt.runtime_state.phase = PHASE_ABORTED
    rt.runtime_state.active_subtask_id = "sub_old"
    rt._runtime_state_store.save(rt.runtime_state)

    # mock conversation to no-op model
    class _Adapter:
        def __init__(self):
            self.sends = 0

        def send(self, messages, schemas):
            self.sends += 1
            return SimpleNamespace(content="ok", tool_calls=[])

    rt.adapter = _Adapter()
    from forge.runtime import SYSTEM_INSTRUCTION  # noqa: F401

    # patch _run_conversation to avoid full stack
    called = {}

    def _fake_conv(task, **kwargs):
        called["task"] = task
        return "done"

    rt._run_conversation = _fake_conv
    out = rt.run("new user task")
    assert rt.runtime_state.phase == PHASE_IDLE
    assert rt.runtime_state.active_subtask_id is None
    assert called.get("task") == "new user task"
    assert out == "done"


def test_handle_human_reply_machine_parse(tmp_path: Path):
    rt = _minimal_runtime(tmp_path)
    assert rt.request_human_intervention(reason="choose").success

    # invalid
    out = rt._handle_human_intervention_reply("maybe later")
    assert "unrecognized" in out or "continue" in out
    assert rt.runtime_state.pending is not None

    # empty modify
    out = rt._handle_human_intervention_reply("modify")
    assert "non-empty" in out or "modify" in out
    assert rt.runtime_state.pending is not None

    rt._run_conversation = lambda task, **k: f"CONV:{task}"
    out = rt._handle_human_intervention_reply("continue")
    assert rt.runtime_state.phase == PHASE_IDLE
    assert out.startswith("CONV:")


def test_gate_blocks_spawn_resume_mutation(tmp_path: Path):
    rt = _minimal_runtime(tmp_path)
    assert rt.request_human_intervention(reason="stop").success

    blocked, summary = human_intervention_pending_blocks(tmp_path)
    assert blocked is True

    g = rt._guard_human_intervention_pending("spawn_subagent")
    assert g is not None and not g.success
    g = rt._guard_human_intervention_pending("resume_subtask")
    assert g is not None and not g.success
    g = rt._guard_human_intervention_pending("str_replace")
    assert g is not None and not g.success
    g = rt._guard_human_intervention_pending("forge_sync")
    assert g is not None and not g.success
    g = rt._guard_human_intervention_pending("todo_write")
    assert g is not None and not g.success
    # resolve and read-like should pass
    assert rt._guard_human_intervention_pending("resolve_human_intervention") is None
    assert rt._guard_human_intervention_pending("read_file") is None


def test_turn_boundary_no_second_send(tmp_path: Path):
    """request_human_intervention ends the turn; adapter must not get a second send."""
    from forge.conversation import Conversation
    from forge.events import EventType
    from forge.runtime import Runtime
    from forge.workspace import Workspace

    class _Adapter:
        def __init__(self):
            self.sends = 0

        def send(self, messages, schemas):
            self.sends += 1
            if self.sends == 1:
                return SimpleNamespace(
                    content="escalating",
                    tool_calls=[
                        ToolCall(
                            id="tc1",
                            name="todo_list",
                            arguments={},
                        ),
                        ToolCall(
                            id="tc2",
                            name="request_human_intervention",
                            arguments={"reason": "need user", "options_context": "c"},
                        ),
                        ToolCall(
                            id="tc3",
                            name="todo_write",
                            arguments={"items": [{"content": "x", "status": "pending"}]},
                        ),
                    ],
                )
            raise AssertionError("adapter must not receive a second send")

    class _Ex:
        def __init__(self):
            self.executed = []

        def execute(self, tc):
            self.executed.append(tc.name)
            return ToolResult.ok(display=f"ran {tc.name}")

    rt = object.__new__(Runtime)
    rt.workspace = Workspace(project_root=str(tmp_path))
    rt.conversation = Conversation()
    rt._handlers = {t: [] for t in EventType}
    rt._pending_action = None
    rt._working_set = None
    rt._last_tool_calls = 0
    rt._last_assistant_replies = []
    rt._last_response_needs_display = False
    rt._runtime_state_store = RuntimeStateStore(tmp_path)
    rt.runtime_state = rt._runtime_state_store.load()
    rt.recovery = rt.runtime_state.recovery
    rt.sync_layer = None
    rt.adapter = _Adapter()
    rt.executor = _Ex()
    rt.memory = None
    rt._on_assistant_delta = None
    rt._on_assistant_done = None

    # Avoid heavy system path: patch _initial_system
    rt._initial_system = lambda extra_system="": "sys"
    # WorkingSet / goal_clarify paths still run

    out = rt._run_conversation("user task")
    assert rt.adapter.sends == 1
    assert rt.executor.executed == []  # todo_list and todo_write not executed
    assert rt.runtime_state.pending is not None
    assert rt.runtime_state.pending.kind == PENDING_KIND_HUMAN_INTERVENTION
    assert "Needs human decision" in out
    loaded = RuntimeStateStore(tmp_path).load()
    assert loaded.pending.kind == PENDING_KIND_HUMAN_INTERVENTION


def test_human_pending_with_checkpoint_not_cleared(tmp_path: Path):
    from forge.subtask_checkpoint import SubtaskCheckpoint, SubtaskCheckpointStore

    rt = _minimal_runtime(tmp_path)
    cp_store = SubtaskCheckpointStore(tmp_path)
    cp = SubtaskCheckpoint(
        subtask_id="sub_abc",
        task={"goal": "g", "done_when": "d", "stop_when": "s"},
        last_tool_call_id="tc_1",
        attempt_count=0,
        updated_at=1.0,
    )
    cp_store.save(cp)
    assert rt.request_human_intervention(reason="pause for user").success
    # checkpoint still present
    loaded_cp = cp_store.load()
    assert loaded_cp is not None
    assert loaded_cp.subtask_id == "sub_abc"
    # resume blocked
    g = rt._guard_human_intervention_pending("resume_subtask")
    assert g is not None


def test_schemas_control_plane_only():
    from forge.tools.schemas import (
        CONTROL_PLANE_TOOLS,
        EXECUTION_PLANE_TOOLS,
    )

    assert "request_human_intervention" in CONTROL_PLANE_TOOLS
    assert "resolve_human_intervention" in CONTROL_PLANE_TOOLS
    assert "request_human_intervention" not in EXECUTION_PLANE_TOOLS
    assert "resolve_human_intervention" not in EXECUTION_PLANE_TOOLS

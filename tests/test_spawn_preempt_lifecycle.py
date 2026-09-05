"""spawn_subagent lifecycle on tool-boundary preempt (phase A).

Preempted need_decision must NOT append JSONL, clear checkpoint, or reset
phase/active. Normal done/blocked still clear and persist.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from forge.agent_abi import (
    STATUS_BLOCKED,
    STATUS_DONE,
    STATUS_NEED_DECISION,
    AgentResult,
    AgentTask,
)
from forge.memory import MemoryStore
from forge.runtime_state import PHASE_IDLE, PHASE_RUNNING_SUBTASK, RuntimeStateStore
from forge.subagent_results_store import load_subagent_results
from forge.subtask_checkpoint import SubtaskCheckpoint, SubtaskCheckpointStore
from forge.workspace import Workspace


def _ar(sid: str, status: str, reason: str) -> AgentResult:
    return AgentResult(
        subtask_id=sid,
        status=status,
        conclusion="c",
        evidence=(),
        uncertain="",
        next="",
        stop_when_met=(status == STATUS_DONE),
        status_reason=reason,
        raw_conclusion="",
    )


def _seed_checkpoint(root, sid: str) -> None:
    SubtaskCheckpointStore(root).save(
        SubtaskCheckpoint(
            subtask_id=sid,
            task={
                "goal": "g",
                "subtask_id": sid,
                "constraints": {},
                "stop_when": "",
                "done_when": "",
                "max_steps": 10,
            },
            last_tool_call_id="tc_seed",
            attempt_count=0,
        )
    )


def _make_runtime(tmp_path):
    from forge.adapters.base import BaseAdapter
    from forge.runtime import Runtime

    class _A(BaseAdapter):
        def send(self, messages, schemas=None, **kwargs):
            raise NotImplementedError

    with patch("forge.runtime.WorldRuntime") as WR:
        WR.return_value.ensure_identity = MagicMock()
        rt = Runtime(
            adapter=_A(),
            workspace=Workspace(project_root=str(tmp_path)),
            memory=MemoryStore(),
        )
    return rt


def _spawn(rt, monkeypatch, result: AgentResult):
    """Call spawn_subagent with run_subagent stubbed to return result."""
    import forge.subagent as sub_mod

    # Align checkpoint id with whatever spawn assigns by capturing after call —
    # instead, patch uuid in spawn path is hard; re-seed after inspecting active id.
    def _fake_run(*a, **k):
        task = a[3] if len(a) > 3 else k.get("task")
        sid = getattr(task, "subtask_id", None) or result.subtask_id
        # Keep seeded checkpoint under the live subtask_id
        root = k.get("project_root") or "."
        store = SubtaskCheckpointStore(root)
        store.save(
            SubtaskCheckpoint(
                subtask_id=str(sid),
                task=task.to_dict() if hasattr(task, "to_dict") else {"goal": "g"},
                last_tool_call_id="tc_live",
                attempt_count=0,
            )
        )
        return AgentResult(
            subtask_id=str(sid),
            status=result.status,
            conclusion=result.conclusion,
            evidence=result.evidence,
            uncertain=result.uncertain,
            next=result.next,
            stop_when_met=result.stop_when_met,
            status_reason=result.status_reason,
            raw_conclusion=result.raw_conclusion,
        )

    monkeypatch.setattr(sub_mod, "run_subagent", _fake_run)
    # Also patch the name as imported inside the closure... run_subagent is
    # imported inside spawn_subagent each call: `from forge.subagent import run_subagent`
    monkeypatch.setattr("forge.subagent.run_subagent", _fake_run)

    fn = rt.executor.tools["spawn_subagent"]
    return fn(goal="test goal", max_steps=10)


@pytest.mark.parametrize(
    "reason",
    [
        "preempted_constraint: constraint_deny_count=2 >= 2",
        "preempted_tool_fail: consecutive_tool_errors=3 >= 3",
        "preempted_budget: steps_used=7 >= 7 (70% of max_steps=10)",
    ],
)
def test_spawn_preempt_preserves_scene(tmp_path, monkeypatch, reason):
    rt = _make_runtime(tmp_path)
    out = _spawn(
        rt,
        monkeypatch,
        _ar("sub_x", STATUS_NEED_DECISION, reason),
    )
    assert out.success
    payload = out.payload or {}
    assert payload.get("preempted") is True
    sid = (payload.get("agent_result") or {}).get("subtask_id")
    assert sid

    # 1) no JSONL record
    assert load_subagent_results(tmp_path).get(sid) is None
    # 2) checkpoint survives
    cp = SubtaskCheckpointStore(tmp_path).load()
    assert cp is not None
    assert cp.subtask_id == sid
    # 3) phase / active retained
    st = RuntimeStateStore(tmp_path).load()
    assert st.phase == PHASE_RUNNING_SUBTASK
    assert st.active_subtask_id == sid


def test_spawn_done_clears_and_appends(tmp_path, monkeypatch):
    rt = _make_runtime(tmp_path)
    out = _spawn(
        rt,
        monkeypatch,
        _ar("sub_d", STATUS_DONE, "stop_when met and v1 done_when proxy satisfied"),
    )
    assert out.success
    sid = (out.payload or {}).get("agent_result", {}).get("subtask_id")
    assert sid
    assert load_subagent_results(tmp_path).get(sid) is not None
    assert SubtaskCheckpointStore(tmp_path).load() is None
    st = RuntimeStateStore(tmp_path).load()
    assert st.phase == PHASE_IDLE
    assert st.active_subtask_id is None


def test_spawn_blocked_clears_and_appends(tmp_path, monkeypatch):
    rt = _make_runtime(tmp_path)
    out = _spawn(
        rt,
        monkeypatch,
        _ar("sub_b", STATUS_BLOCKED, "max_steps reached without stop_when met"),
    )
    assert out.success
    sid = (out.payload or {}).get("agent_result", {}).get("subtask_id")
    assert sid
    assert load_subagent_results(tmp_path).get(sid) is not None
    assert SubtaskCheckpointStore(tmp_path).load() is None
    st = RuntimeStateStore(tmp_path).load()
    assert st.phase == PHASE_IDLE
    assert st.active_subtask_id is None


def test_resume_after_preempt(tmp_path, monkeypatch):
    """After preempt scene preserved, resume_subtask can b2-rebuild."""
    rt = _make_runtime(tmp_path)
    out = _spawn(
        rt,
        monkeypatch,
        _ar("sub_r", STATUS_NEED_DECISION, "preempted_constraint: x"),
    )
    sid = (out.payload or {}).get("agent_result", {}).get("subtask_id")
    assert SubtaskCheckpointStore(tmp_path).load() is not None

    calls = {"n": 0}

    def _resume_run(*a, **k):
        calls["n"] += 1
        task = a[3] if len(a) > 3 else k.get("task")
        return AgentResult(
            subtask_id=str(getattr(task, "subtask_id", sid)),
            status=STATUS_DONE,
            conclusion="resumed ok",
            evidence=(),
            uncertain="",
            next="",
            stop_when_met=True,
            status_reason="stop_when met and v1 done_when proxy satisfied",
            raw_conclusion="",
        )

    monkeypatch.setattr("forge.subagent.run_subagent", _resume_run)
    resume = rt.executor.tools["resume_subtask"]
    r = resume(subtask_id=sid)
    assert r.success, getattr(r, "display", r)
    assert calls["n"] == 1
    # terminal done should clear
    assert SubtaskCheckpointStore(tmp_path).load() is None
    loaded = load_subagent_results(tmp_path).get(sid)
    assert loaded is not None
    # precheck may demote done→blocked when evidence empty; still terminal
    assert loaded["status"] in (STATUS_DONE, STATUS_BLOCKED)


def test_abort_after_preempt(tmp_path, monkeypatch):
    """After preempt, abort_subtask synthesizes blocked and clears checkpoint."""
    rt = _make_runtime(tmp_path)
    out = _spawn(
        rt,
        monkeypatch,
        _ar("sub_a", STATUS_NEED_DECISION, "preempted_tool_fail: x"),
    )
    sid = (out.payload or {}).get("agent_result", {}).get("subtask_id")
    assert SubtaskCheckpointStore(tmp_path).load() is not None

    abort = rt.executor.tools["abort_subtask"]
    r = abort(subtask_id=sid)
    assert r.success, getattr(r, "display", r)
    assert SubtaskCheckpointStore(tmp_path).load() is None
    loaded = load_subagent_results(tmp_path).get(sid)
    assert loaded is not None
    assert loaded["status"] == STATUS_BLOCKED
    st = RuntimeStateStore(tmp_path).load()
    assert st.phase == PHASE_IDLE

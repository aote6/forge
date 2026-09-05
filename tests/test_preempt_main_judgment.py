"""Phase B: Main AI judgment / HI boundary for preempted_* handoff."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from forge.agent_abi import (
    STATUS_BLOCKED,
    STATUS_NEED_DECISION,
    AgentResult,
)
from forge.memory import MemoryStore
from forge.runtime_state import (
    PHASE_AWAITING_USER,
    PHASE_IDLE,
    PHASE_RUNNING_SUBTASK,
    RuntimeStateStore,
)
from forge.subagent_results_store import load_subagent_results
from forge.subtask_checkpoint import SubtaskCheckpoint, SubtaskCheckpointStore
from forge.system_prompt import SYSTEM_INSTRUCTION
from forge.workspace import Workspace


def _ar(sid: str, status: str, reason: str) -> AgentResult:
    return AgentResult(
        subtask_id=sid,
        status=status,
        conclusion="c",
        evidence=(),
        uncertain="",
        next="",
        stop_when_met=False,
        status_reason=reason,
        raw_conclusion="",
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


def _spawn_preempt(rt, monkeypatch, reason="preempted_constraint: x"):
    import forge.subagent as sub_mod

    def _fake_run(*a, **k):
        task = a[3] if len(a) > 3 else k.get("task")
        sid = getattr(task, "subtask_id", None) or "sub_x"
        root = k.get("project_root") or "."
        SubtaskCheckpointStore(root).save(
            SubtaskCheckpoint(
                subtask_id=str(sid),
                task=task.to_dict() if hasattr(task, "to_dict") else {"goal": "g"},
                last_tool_call_id="tc_live",
                attempt_count=0,
            )
        )
        return _ar(str(sid), STATUS_NEED_DECISION, reason)

    monkeypatch.setattr(sub_mod, "run_subagent", _fake_run)
    monkeypatch.setattr("forge.subagent.run_subagent", _fake_run)
    out = rt.executor.tools["spawn_subagent"](goal="test goal", max_steps=10)
    assert out.success
    sid = (out.payload or {}).get("agent_result", {}).get("subtask_id")
    assert sid
    assert getattr(rt, "_preempt_handoff_subtask_id", None) == sid
    return sid


def test_prompt_contains_preempt_judgment_contract():
    text = SYSTEM_INSTRUCTION
    assert "Preempted Subtask Judgment" in text
    assert "preempted_" in text
    assert "禁止无阅读 reason" in text or "无判断就直接 resume" in text
    # Durable Pause still user-gated
    assert "仅在用户明确要求「继续该子任务」时调用 resume_subtask" in text
    # HI allowed for preempt preference fork
    assert "preempted_*" in text
    assert "允许" in text and "request_human_intervention" in text


def test_preempt_main_can_resume(tmp_path, monkeypatch):
    rt = _make_runtime(tmp_path)
    sid = _spawn_preempt(rt, monkeypatch)
    assert SubtaskCheckpointStore(tmp_path).load() is not None

    def _resume_run(*a, **k):
        task = a[3] if len(a) > 3 else k.get("task")
        return _ar(
            str(getattr(task, "subtask_id", sid)),
            STATUS_BLOCKED,
            "max_steps reached without stop_when met",
        )

    monkeypatch.setattr("forge.subagent.run_subagent", _resume_run)
    r = rt.executor.tools["resume_subtask"](subtask_id=sid)
    assert r.success, r.display
    assert getattr(rt, "_preempt_handoff_subtask_id", None) is None


def test_preempt_main_can_abort(tmp_path, monkeypatch):
    rt = _make_runtime(tmp_path)
    sid = _spawn_preempt(rt, monkeypatch)
    r = rt.executor.tools["abort_subtask"](subtask_id=sid)
    assert r.success, r.display
    assert SubtaskCheckpointStore(tmp_path).load() is None
    assert getattr(rt, "_preempt_handoff_subtask_id", None) is None
    st = RuntimeStateStore(tmp_path).load()
    assert st.phase == PHASE_IDLE


def test_preempt_allows_human_intervention(tmp_path, monkeypatch):
    rt = _make_runtime(tmp_path)
    sid = _spawn_preempt(rt, monkeypatch)
    st = RuntimeStateStore(tmp_path).load()
    assert st.phase == PHASE_RUNNING_SUBTASK
    assert st.active_subtask_id == sid

    res = rt.request_human_intervention(
        reason="preference fork after preempt",
        options_context=f"subtask_id={sid}",
        proposed_next="pick path A or B",
    )
    assert res.success, res.display
    assert rt.runtime_state.phase == PHASE_AWAITING_USER
    assert rt.runtime_state.active_subtask_id is None
    assert getattr(rt, "_preempt_handoff_subtask_id", None) is None
    assert rt.runtime_state.pending is not None
    assert rt.runtime_state.pending.kind == "human_intervention"


def test_hi_continue_after_preempt_is_respawn_not_auto_resume(tmp_path, monkeypatch):
    """HI continue clears pending; checkpoint may remain — Main must re-spawn, not auto-resume."""
    rt = _make_runtime(tmp_path)
    sid = _spawn_preempt(rt, monkeypatch)
    assert rt.request_human_intervention(reason="need user preference").success

    # checkpoint still on disk (lifecycle not auto-cleared by HI open)
    # resolve continue → IDLE, no auto resume_subtask call
    r = rt.resolve_human_intervention(decision="continue")
    assert r.success, r.display
    assert rt.runtime_state.phase == PHASE_IDLE
    assert rt.runtime_state.pending is None
    # handoff already cleared; resume is not invoked by resolve
    assert getattr(rt, "_preempt_handoff_subtask_id", None) is None


def test_hi_abort_after_preempt_cleans_pending(tmp_path, monkeypatch):
    rt = _make_runtime(tmp_path)
    _spawn_preempt(rt, monkeypatch)
    assert rt.request_human_intervention(reason="stop").success
    r = rt.resolve_human_intervention(decision="abort")
    assert r.success, r.display
    # resolve abort sets ABORTED or similar — pending cleared
    assert rt.runtime_state.pending is None


def test_hi_refused_during_normal_active_subtask(tmp_path, monkeypatch):
    """Non-preempt RUNNING_SUBTASK + active → HI still refused."""
    rt = _make_runtime(tmp_path)
    rt.runtime_state.phase = PHASE_RUNNING_SUBTASK
    rt.runtime_state.active_subtask_id = "sub_running"
    rt.runtime_state.refresh_recovery()
    rt._runtime_state_store.save(rt.runtime_state)
    # no handoff mark
    rt._preempt_handoff_subtask_id = None
    res = rt.request_human_intervention(reason="should fail")
    assert not res.success
    assert "refused" in (res.display or "").lower() or "IDLE" in (res.display or "")


def test_process_interrupt_recovery_hi_cannot_bypass(tmp_path, monkeypatch):
    """Checkpoint without preempt handoff (crash recovery) → HI cannot bypass user gate."""
    rt = _make_runtime(tmp_path)
    sid = "sub_crash"
    SubtaskCheckpointStore(tmp_path).save(
        SubtaskCheckpoint(
            subtask_id=sid,
            task={"goal": "g", "subtask_id": sid, "max_steps": 5},
            last_tool_call_id="tc_c",
            attempt_count=0,
        )
    )
    # Simulate recovery-ish state without handoff flag
    rt.runtime_state.phase = PHASE_RUNNING_SUBTASK
    rt.runtime_state.active_subtask_id = sid
    rt.runtime_state.refresh_recovery()
    rt._runtime_state_store.save(rt.runtime_state)
    rt._preempt_handoff_subtask_id = None

    res = rt.request_human_intervention(reason="bypass attempt")
    assert not res.success

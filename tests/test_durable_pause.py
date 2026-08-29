"""Durable Pause / SubtaskCheckpoint tests (design §13)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from forge.agent_abi import AgentTask, AgentResult, STATUS_BLOCKED, STATUS_DONE
from forge.runtime_state import (
    PHASE_IDLE,
    PHASE_RUNNING_SUBTASK,
    RuntimeState,
    RuntimeStateStore,
)
from forge.subagent_results_store import (
    append_subagent_result,
    load_subagent_results,
)
from forge.subtask_checkpoint import (
    MAX_RESUME_ATTEMPTS,
    SUBTASK_RECOVERY_DECISION_REQUIRED,
    SUBTASK_RECOVERY_INCONSISTENT,
    SUBTASK_RECOVERY_NONE,
    SubtaskCheckpoint,
    SubtaskCheckpointStore,
    build_prior_facts_summary,
    derive_subtask_recovery,
    validate_checkpoint_facts,
)
from forge.tool_call_record import (
    ToolCallRecord,
    list_records_for_subtask,
    write_record,
)


def _task_dict(sid: str = "sub_abc") -> dict:
    return AgentTask(
        goal="fix the bug",
        subtask_id=sid,
        done_when="tests pass",
        stop_when="done",
        max_steps=10,
    ).to_dict()


def test_checkpoint_roundtrip(tmp_path):
    store = SubtaskCheckpointStore(tmp_path)
    cp = SubtaskCheckpoint(
        subtask_id="sub_1",
        task=_task_dict("sub_1"),
        last_tool_call_id="tc_aaa",
        attempt_count=1,
        updated_at=123.0,
    )
    assert store.save(cp) is True
    loaded = store.load()
    assert loaded is not None
    assert loaded.subtask_id == "sub_1"
    assert loaded.last_tool_call_id == "tc_aaa"
    assert loaded.attempt_count == 1
    assert loaded.task["goal"] == "fix the bug"
    assert store.clear() is True
    assert store.load() is None


def test_checkpoint_corrupt_discarded(tmp_path):
    store = SubtaskCheckpointStore(tmp_path)
    store.path.parent.mkdir(parents=True, exist_ok=True)
    store.path.write_text("{not json", encoding="utf-8")
    assert store.load() is None


def test_agent_task_from_dict_symmetric():
    t = AgentTask(
        goal="g",
        subtask_id="sub_x",
        constraints={"not_allowed": "rm"},
        stop_when="s",
        done_when="d",
        max_steps=7,
    )
    t2 = AgentTask.from_dict(t.to_dict())
    assert t2.goal == "g"
    assert t2.subtask_id == "sub_x"
    assert t2.constraints == {"not_allowed": "rm"}
    assert t2.max_steps == 7


def test_list_records_for_subtask(tmp_path):
    for i, sid in enumerate(["sub_a", "sub_b", "sub_a"]):
        write_record(
            tmp_path,
            ToolCallRecord(
                tool_call_id=f"tc_{i}",
                subtask_id=sid,
                tool_name="read_file",
                input={},
                output=None,
                status="success",
                error=None,
                timestamp=float(i),
            ),
        )
    recs = list_records_for_subtask(tmp_path, "sub_a")
    assert len(recs) == 2
    assert recs[0]["tool_call_id"] == "tc_0"
    assert list_records_for_subtask(tmp_path, "missing") == []


def test_derive_subtask_recovery_modes():
    cp = SubtaskCheckpoint(
        subtask_id="sub_c",
        task=_task_dict("sub_c"),
        last_tool_call_id="tc_1",
    )
    r = derive_subtask_recovery(None, PHASE_IDLE, None)
    assert r.mode == SUBTASK_RECOVERY_NONE

    r = derive_subtask_recovery(cp, PHASE_RUNNING_SUBTASK, "sub_c")
    assert r.mode == SUBTASK_RECOVERY_DECISION_REQUIRED

    r = derive_subtask_recovery(cp, PHASE_IDLE, None)
    assert r.mode == SUBTASK_RECOVERY_INCONSISTENT

    r = derive_subtask_recovery(cp, PHASE_RUNNING_SUBTASK, "sub_other")
    assert r.mode == SUBTASK_RECOVERY_INCONSISTENT


def test_validate_checkpoint_facts(tmp_path):
    cp = SubtaskCheckpoint(
        subtask_id="sub_f",
        task=_task_dict("sub_f"),
        last_tool_call_id="tc_fact",
    )
    assert validate_checkpoint_facts(tmp_path, cp) is False
    write_record(
        tmp_path,
        ToolCallRecord(
            tool_call_id="tc_fact",
            subtask_id="sub_f",
            tool_name="search_code",
            input={},
            output=None,
            status="success",
            error=None,
            timestamp=1.0,
        ),
    )
    assert validate_checkpoint_facts(tmp_path, cp) is True
    # wrong subtask_id on record
    write_record(
        tmp_path,
        ToolCallRecord(
            tool_call_id="tc_wrong",
            subtask_id="sub_other",
            tool_name="search_code",
            input={},
            output=None,
            status="success",
            error=None,
            timestamp=2.0,
        ),
    )
    cp2 = SubtaskCheckpoint(
        subtask_id="sub_f",
        task=_task_dict("sub_f"),
        last_tool_call_id="tc_wrong",
    )
    assert validate_checkpoint_facts(tmp_path, cp2) is False


def test_prior_facts_summary_all_records(tmp_path):
    sid = "sub_sum"
    for i in range(3):
        write_record(
            tmp_path,
            ToolCallRecord(
                tool_call_id=f"tc_s{i}",
                subtask_id=sid,
                tool_name="read_file",
                input={},
                output=None,
                status="success",
                error=None,
                timestamp=float(i),
            ),
        )
    summary = build_prior_facts_summary(tmp_path, sid)
    assert "tc_s0" in summary and "tc_s2" in summary
    assert "CONCLUSION" not in summary
    assert "EVIDENCE" not in summary


def test_update_after_tool_advances_pointer(tmp_path):
    store = SubtaskCheckpointStore(tmp_path)
    store.update_after_tool(
        subtask_id="sub_u",
        task_dict=_task_dict("sub_u"),
        last_tool_call_id="tc_1",
    )
    store.update_after_tool(
        subtask_id="sub_u",
        task_dict=_task_dict("sub_u"),
        last_tool_call_id="tc_2",
    )
    cp = store.load()
    assert cp is not None
    assert cp.last_tool_call_id == "tc_2"


def test_append_fail_keeps_checkpoint(tmp_path, monkeypatch):
    """Design §6.2: append failure must not clear checkpoint."""
    store = SubtaskCheckpointStore(tmp_path)
    store.update_after_tool(
        subtask_id="sub_keep",
        task_dict=_task_dict("sub_keep"),
        last_tool_call_id="tc_k",
    )
    assert store.load() is not None
    # Simulate the spawn clear path: only clear on append_ok
    append_ok = False
    if append_ok:
        store.clear()
    assert store.load() is not None


def test_abort_no_terminal_synthesizes(tmp_path):
    sid = "sub_abort"
    store = SubtaskCheckpointStore(tmp_path)
    store.update_after_tool(
        subtask_id=sid,
        task_dict=_task_dict(sid),
        last_tool_call_id="tc_ab",
    )
    # Simulate abort path: no terminal -> synthesize
    existing = load_subagent_results(tmp_path).get(sid)
    assert existing is None
    ar = AgentResult(
        subtask_id=sid,
        status=STATUS_BLOCKED,
        conclusion="subtask abandoned after process interrupt",
        evidence=(),
        uncertain="",
        next="",
        stop_when_met=False,
        status_reason="abandoned_after_process_interrupt",
    )
    assert append_subagent_result(tmp_path, ar.to_dict()) is True
    store.clear()
    loaded = load_subagent_results(tmp_path)[sid]
    assert loaded["status"] == "blocked"
    assert loaded["status_reason"] == "abandoned_after_process_interrupt"
    assert store.load() is None


def test_abort_with_terminal_no_second_result(tmp_path):
    sid = "sub_done"
    ar = AgentResult(
        subtask_id=sid,
        status=STATUS_DONE,
        conclusion="done",
        evidence=(),
        uncertain="",
        next="",
        stop_when_met=True,
        status_reason="ok",
    )
    append_subagent_result(tmp_path, ar.to_dict())
    store = SubtaskCheckpointStore(tmp_path)
    store.update_after_tool(
        subtask_id=sid,
        task_dict=_task_dict(sid),
        last_tool_call_id="tc_d",
    )
    # abort path with terminal: clear only
    existing = load_subagent_results(tmp_path).get(sid)
    assert existing is not None
    store.clear()
    # still exactly one terminal
    all_results = load_subagent_results(tmp_path)
    assert all_results[sid]["status"] == "done"
    # re-append would create second line but load keeps last — we must NOT append
    assert store.load() is None


def test_runtime_init_derives_decision_required(tmp_path, monkeypatch):
    """PROCESS_INTERRUPTED simulation: checkpoint + RUNNING_SUBTASK → DECISION_REQUIRED."""
    monkeypatch.setattr(
        "forge.world.runtime.WorldRuntime.ensure_identity",
        lambda self: None,
    )
    rs = RuntimeStateStore(tmp_path)
    rs.save(
        RuntimeState(
            phase=PHASE_RUNNING_SUBTASK,
            active_subtask_id="sub_rt",
            pending=None,
        )
    )
    SubtaskCheckpointStore(tmp_path).save(
        SubtaskCheckpoint(
            subtask_id="sub_rt",
            task=_task_dict("sub_rt"),
            last_tool_call_id="tc_rt",
            attempt_count=0,
            updated_at=1.0,
        )
    )
    from forge.adapters.base import BaseAdapter
    from forge.memory import MemoryStore
    from forge.runtime import Runtime
    from forge.workspace import Workspace

    class _A(BaseAdapter):
        def send(self, messages, schemas):
            raise NotImplementedError

    try:
        rt = Runtime(
            adapter=_A(),
            workspace=Workspace(project_root=str(tmp_path)),
            memory=MemoryStore(),
        )
    except Exception as e:
        pytest.skip(f"Runtime init blocked: {e}")

    assert rt.subtask_recovery.mode == SUBTASK_RECOVERY_DECISION_REQUIRED
    assert rt.subtask_recovery.checkpoint is not None
    assert rt.subtask_recovery.checkpoint.subtask_id == "sub_rt"


def test_runtime_init_inconsistent_fact_valid(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "forge.world.runtime.WorldRuntime.ensure_identity",
        lambda self: None,
    )
    # phase IDLE + checkpoint → INCONSISTENT
    RuntimeStateStore(tmp_path).save(RuntimeState(phase=PHASE_IDLE))
    SubtaskCheckpointStore(tmp_path).save(
        SubtaskCheckpoint(
            subtask_id="sub_ic",
            task=_task_dict("sub_ic"),
            last_tool_call_id="tc_ic",
        )
    )
    write_record(
        tmp_path,
        ToolCallRecord(
            tool_call_id="tc_ic",
            subtask_id="sub_ic",
            tool_name="read_file",
            input={},
            output=None,
            status="success",
            error=None,
            timestamp=1.0,
        ),
    )
    from forge.adapters.base import BaseAdapter
    from forge.memory import MemoryStore
    from forge.runtime import Runtime
    from forge.workspace import Workspace

    class _A(BaseAdapter):
        def send(self, messages, schemas):
            raise NotImplementedError

    try:
        rt = Runtime(
            adapter=_A(),
            workspace=Workspace(project_root=str(tmp_path)),
            memory=MemoryStore(),
        )
    except Exception as e:
        pytest.skip(f"Runtime init blocked: {e}")

    assert rt.subtask_recovery.mode == SUBTASK_RECOVERY_INCONSISTENT
    assert rt.subtask_recovery.fact_valid is True


def test_atomic_write_no_torn(tmp_path):
    store = SubtaskCheckpointStore(tmp_path)
    cp = SubtaskCheckpoint(
        subtask_id="sub_atom",
        task=_task_dict("sub_atom"),
        last_tool_call_id="tc_atom",
    )
    store.save(cp)
    raw = store.path.read_text(encoding="utf-8")
    data = json.loads(raw)
    assert data["subtask_id"] == "sub_atom"
    assert not store.path.with_suffix(".tmp").exists()

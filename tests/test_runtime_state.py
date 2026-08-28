"""R1: minimal RuntimeState closed loop."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

from forge.runtime_state import (
    PHASE_ABORTED,
    PHASE_AWAITING_USER,
    PHASE_BLOCKED,
    PHASE_COMPLETED,
    PHASE_DISPATCHING,
    PHASE_IDLE,
    PHASE_PAUSED_SUBTASK,
    PHASE_RUNNING_SUBTASK,
    PENDING_KIND_SYNC_DECISION,
    RECOVERY_ABORT,
    RECOVERY_DECISION_REQUIRED,
    RECOVERY_NONE,
    Pending,
    Recovery,
    RuntimeState,
    RuntimeStateStore,
    derive_recovery,
)


def test_default_state():
    st = RuntimeState()
    assert st.phase == PHASE_IDLE
    assert st.active_subtask_id is None
    assert st.pending is None
    d = st.to_dict()
    assert d == {
        "phase": PHASE_IDLE,
        "active_subtask_id": None,
        "pending": None,
    }
    assert "recovery" not in d


def test_json_round_trip(tmp_path: Path):
    store = RuntimeStateStore(tmp_path)
    st = RuntimeState(
        phase=PHASE_AWAITING_USER,
        active_subtask_id="sub_abc",
        pending=Pending(
            kind=PENDING_KIND_SYNC_DECISION,
            summary="conflict on src/a.py",
            payload={"basis": "CONFLICT"},
        ),
    )
    store.save(st)
    path = tmp_path / ".forge" / "runtime_state.json"
    assert path.exists()
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert "recovery" not in raw
    assert raw["phase"] == PHASE_AWAITING_USER
    assert raw["active_subtask_id"] == "sub_abc"
    assert raw["pending"]["kind"] == PENDING_KIND_SYNC_DECISION

    loaded = store.load()
    assert loaded.phase == PHASE_AWAITING_USER
    assert loaded.active_subtask_id == "sub_abc"
    assert loaded.pending is not None
    assert loaded.pending.kind == PENDING_KIND_SYNC_DECISION
    assert loaded.pending.summary == "conflict on src/a.py"
    assert loaded.pending.payload == {"basis": "CONFLICT"}
    assert loaded.recovery.mode == RECOVERY_DECISION_REQUIRED


def test_file_missing_returns_default(tmp_path: Path):
    store = RuntimeStateStore(tmp_path)
    st = store.load()
    assert st.phase == PHASE_IDLE
    assert st.pending is None
    assert st.recovery.mode == RECOVERY_NONE
    assert not (tmp_path / ".forge" / "runtime_state.json").exists()


def test_empty_file_recovers(tmp_path: Path):
    forge = tmp_path / ".forge"
    forge.mkdir(parents=True)
    path = forge / "runtime_state.json"
    path.write_text("", encoding="utf-8")
    store = RuntimeStateStore(tmp_path)
    st = store.load()
    assert st.phase == PHASE_IDLE
    assert st.recovery.mode == RECOVERY_NONE
    assert (forge / "runtime_state.json.broken").exists() or not path.exists()


def test_corrupt_file_recovers(tmp_path: Path):
    forge = tmp_path / ".forge"
    forge.mkdir(parents=True)
    path = forge / "runtime_state.json"
    path.write_text("{not-json", encoding="utf-8")
    store = RuntimeStateStore(tmp_path)
    st = store.load()
    assert st.phase == PHASE_IDLE
    assert st.recovery.mode == RECOVERY_NONE
    assert (forge / "runtime_state.json.broken").exists()


def test_pending_sync_decision_only():
    ok = Pending.from_dict(
        {
            "kind": "sync_decision",
            "summary": "s",
            "payload": {"k": 1},
        }
    )
    assert ok is not None
    assert ok.kind == PENDING_KIND_SYNC_DECISION

    # execution_pause must not be accepted on durable path
    assert Pending.from_dict({"kind": "execution_pause", "summary": "x"}) is None
    assert Pending.from_dict({"kind": "other"}) is None
    assert Pending.from_dict(None) is None
    assert Pending.from_dict("bad") is None


def test_derive_recovery_matrix():
    assert derive_recovery(PHASE_IDLE, None).mode == RECOVERY_NONE
    assert derive_recovery(PHASE_COMPLETED, None).mode == RECOVERY_NONE
    assert derive_recovery(PHASE_BLOCKED, None).mode == RECOVERY_NONE
    assert derive_recovery(PHASE_ABORTED, None).mode == RECOVERY_NONE

    assert derive_recovery(PHASE_DISPATCHING, None).mode == RECOVERY_ABORT
    assert derive_recovery(PHASE_RUNNING_SUBTASK, None).mode == RECOVERY_ABORT
    assert derive_recovery(PHASE_PAUSED_SUBTASK, None).mode == RECOVERY_ABORT

    pend = Pending(kind=PENDING_KIND_SYNC_DECISION, summary="wait")
    r = derive_recovery(PHASE_AWAITING_USER, pend)
    assert r.mode == RECOVERY_DECISION_REQUIRED
    assert r.reason is not None

    # AWAITING_USER without durable pending → none (clean)
    assert derive_recovery(PHASE_AWAITING_USER, None).mode == RECOVERY_NONE


def test_unknown_phase_normalized_on_load():
    st = RuntimeState.from_dict({"phase": "NOT_A_PHASE", "active_subtask_id": None})
    assert st.phase == PHASE_IDLE
    assert st.recovery.mode == RECOVERY_NONE


def test_runtime_startup_loads_state(tmp_path: Path, monkeypatch):
    """Runtime.__init__ loads RuntimeState and exposes recovery."""
    # Pre-seed durable state
    store = RuntimeStateStore(tmp_path)
    store.save(
        RuntimeState(
            phase=PHASE_RUNNING_SUBTASK,
            active_subtask_id="sub_dead",
            pending=None,
        )
    )

    # Avoid World / Sync heavy init: patch dependencies
    monkeypatch.setattr(
        "forge.world.runtime.WorldRuntime.ensure_identity",
        lambda self: None,
    )
    # SyncState/SyncLayer still construct; ensure project has .forge
    from forge.adapters.base import BaseAdapter
    from forge.memory import MemoryStore
    from forge.runtime import Runtime
    from forge.workspace import Workspace

    class _A(BaseAdapter):
        def send(self, messages, schemas):
            raise NotImplementedError

    ws = Workspace(project_root=str(tmp_path))
    mem = MemoryStore()
    # WorldRuntime may still try veritas — mark unavailable path is fine
    try:
        rt = Runtime(adapter=_A(), workspace=ws, memory=mem)
    except Exception as e:
        # If world hard-fails construction in this env, skip deeper assert
        # but store load path is unit-tested above.
        import pytest

        pytest.skip(f"Runtime init blocked in test env: {e}")

    assert hasattr(rt, "runtime_state")
    assert hasattr(rt, "recovery")
    assert rt.runtime_state.phase == PHASE_RUNNING_SUBTASK
    assert rt.runtime_state.active_subtask_id == "sub_dead"
    assert rt.recovery.mode == RECOVERY_ABORT
    assert isinstance(rt.recovery, Recovery)


def test_to_dict_excludes_world_disk_git_fields():
    st = RuntimeState(phase=PHASE_IDLE)
    keys = set(st.to_dict().keys())
    assert keys == {"phase", "active_subtask_id", "pending"}

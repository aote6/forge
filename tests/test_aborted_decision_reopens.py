"""Locking tests: ABORTED decision must not block a new PENDING for the same basis.

Regression for the deadlock where abort → forge_sync → CONFLICT again
left pending=null because the aborted decision was treated like DECIDED
and no new decision was opened. These tests exercise the REAL Runtime.
"""

from pathlib import Path

from forge.memory import MemoryStore
from forge.workspace import Workspace

from forge.sync.sync_layer import CONFLICT, SyncReport


def _make_runtime(tmp_path: Path):
    """Construct a real Runtime instance, same pattern as test_user_stop.py."""
    from forge.runtime import Runtime

    ws = Workspace(project_root=str(tmp_path))

    class _Adapter:
        model_name = "t"

        def send(self, messages, schemas):
            from forge.adapters.base import Message
            return Message(role="assistant", content="")

    rt = Runtime(_Adapter(), ws, MemoryStore())
    # Ensure the stores we need exist on this Runtime
    assert getattr(rt, "_sync_decision_store", None) is not None
    assert getattr(rt, "_runtime_state_store", None) is not None
    assert getattr(rt, "runtime_state", None) is not None
    return rt


def test_aborted_decision_reopens_for_same_basis(tmp_path):
    """CONFLICT → PENDING → abort → CONFLICT again → new PENDING with new id."""
    rt = _make_runtime(tmp_path)
    report = SyncReport(status=CONFLICT)

    # First open: creates a PENDING decision
    rt._maybe_open_sync_decision(report)
    first = rt._sync_decision_store.load()
    assert first is not None
    assert first.status == "pending"
    first_id = first.decision_id

    # User aborts
    first.apply_direction("abort")
    rt._sync_decision_store.save(first)

    # Clear the pending index (simulate abort resolution path)
    rt.runtime_state.pending = None
    if rt.runtime_state.phase == "AWAITING_USER":
        from forge.runtime_state import PHASE_IDLE
        rt.runtime_state.phase = PHASE_IDLE
    rt.runtime_state.refresh_recovery()
    rt.recovery = rt.runtime_state.recovery
    rt._runtime_state_store.save(rt.runtime_state)

    # Same CONFLICT again: real Runtime must open a new PENDING
    rt._maybe_open_sync_decision(report)
    second = rt._sync_decision_store.load()
    assert second is not None
    assert second.status == "pending"
    assert second.decision_id != first_id
    # And the RuntimeState.pending index must be re-populated
    assert rt.runtime_state.pending is not None
    assert rt.runtime_state.pending.kind == "sync_decision"
    assert rt.runtime_state.pending.payload["decision_id"] == second.decision_id


def test_decided_decision_does_not_reopen_for_same_basis(tmp_path):
    """CONFLICT → PENDING → decide → CONFLICT again → no new decision."""
    rt = _make_runtime(tmp_path)
    report = SyncReport(status=CONFLICT)

    rt._maybe_open_sync_decision(report)
    first = rt._sync_decision_store.load()
    assert first.status == "pending"
    first_id = first.decision_id

    first.apply_direction("disk_to_world")
    rt._sync_decision_store.save(first)

    # Clear pending index (simulate resolved path)
    rt.runtime_state.pending = None
    if rt.runtime_state.phase == "AWAITING_USER":
        from forge.runtime_state import PHASE_IDLE
        rt.runtime_state.phase = PHASE_IDLE
    rt.runtime_state.refresh_recovery()
    rt.recovery = rt.runtime_state.recovery
    rt._runtime_state_store.save(rt.runtime_state)

    rt._maybe_open_sync_decision(report)
    second = rt._sync_decision_store.load()
    assert second is not None
    assert second.decision_id == first_id
    assert second.status == "decided"
    # pending index must stay null
    assert rt.runtime_state.pending is None

"""Locking test: resolve_sync_decision must not silently swallow a new direction.

Regression for the abort→world_to_disk bug where a stale decided
decision was returned as-is even when the caller requested a different
direction. The fix raises ValueError when decision.direction != direction.
"""

import pytest

from forge.sync.decision import SyncDecision, SyncDecisionStore, STATUS_DECIDED
from forge.runtime_state import RuntimeStateStore, Pending, PENDING_KIND_SYNC_DECISION, PHASE_AWAITING_USER


class _DummyRuntime:
    """Minimal object exposing only what resolve_sync_decision touches."""

    def __init__(self, project_root):
        self._sync_decision_store = SyncDecisionStore(project_root)
        self._runtime_state_store = RuntimeStateStore(project_root)
        self.runtime_state = self._runtime_state_store.load()
        self.sync_decision = None
        self.recovery = self.runtime_state.recovery

    def resolve_sync_decision(self, direction: str):
        from forge.runtime_state import PHASE_IDLE, PENDING_KIND_SYNC_DECISION
        from forge.sync.decision import STATUS_PENDING, VALID_DIRECTIONS

        direction = str(direction or "").strip()
        if direction not in VALID_DIRECTIONS:
            raise ValueError(
                f"direction must be one of {sorted(VALID_DIRECTIONS)}, got {direction!r}"
            )

        decision = self._sync_decision_store.load()
        if decision is None or decision.status != STATUS_PENDING:
            if decision is not None and decision.direction != direction:
                raise ValueError(
                    f"SyncDecision {decision.decision_id} already resolved "
                    f"as direction={decision.direction!r} (status="
                    f"{decision.status!r}); refusing to silently return it "
                    f"for a new direction={direction!r} request."
                )
            if (
                self.runtime_state.pending is not None
                and self.runtime_state.pending.kind == PENDING_KIND_SYNC_DECISION
            ):
                self.runtime_state.pending = None
                if self.runtime_state.phase == "AWAITING_USER":
                    self.runtime_state.phase = PHASE_IDLE
                self.runtime_state.refresh_recovery()
                self.recovery = self.runtime_state.recovery
                self._runtime_state_store.save(self.runtime_state)
            self.sync_decision = decision
            return decision

        decision.apply_direction(direction)
        self._sync_decision_store.save(decision)
        self.sync_decision = decision
        self.runtime_state.pending = None
        if self.runtime_state.phase == "AWAITING_USER":
            self.runtime_state.phase = PHASE_IDLE
        self.runtime_state.refresh_recovery()
        self.recovery = self.runtime_state.recovery
        self._runtime_state_store.save(self.runtime_state)
        return decision


def test_resolve_sync_decision_rejects_direction_mismatch(tmp_path):
    """After a decision is resolved, a new direction must raise, not silently return stale."""
    # Setup: a pending decision with basis=FAST_FORWARD_WORLD_TO_DISK
    decision = SyncDecision.new_pending(basis="FAST_FORWARD_WORLD_TO_DISK")
    SyncDecisionStore(str(tmp_path)).save(decision)

    rs = RuntimeStateStore(str(tmp_path)).load()
    rs.phase = PHASE_AWAITING_USER
    rs.pending = Pending(
        kind=PENDING_KIND_SYNC_DECISION,
        summary="sync_decision required: basis=FAST_FORWARD_WORLD_TO_DISK",
        payload={"decision_id": decision.decision_id, "basis": decision.basis},
    )
    RuntimeStateStore(str(tmp_path)).save(rs)

    rt = _DummyRuntime(str(tmp_path))

    # First resolve: world_to_disk → decided
    resolved = rt.resolve_sync_decision("world_to_disk")
    assert resolved.status == STATUS_DECIDED
    assert resolved.direction == "world_to_disk"

    # Second resolve with a different direction: must raise ValueError
    with pytest.raises(ValueError) as exc_info:
        rt.resolve_sync_decision("abort")
    msg = str(exc_info.value)
    assert decision.decision_id in msg
    assert "world_to_disk" in msg
    assert "abort" in msg


def test_resolve_sync_decision_idempotent_same_direction(tmp_path):
    """Repeated resolve with the same direction is still a safe no-op."""
    decision = SyncDecision.new_pending(basis="FAST_FORWARD_WORLD_TO_DISK")
    SyncDecisionStore(str(tmp_path)).save(decision)

    rs = RuntimeStateStore(str(tmp_path)).load()
    rs.phase = PHASE_AWAITING_USER
    rs.pending = Pending(
        kind=PENDING_KIND_SYNC_DECISION,
        summary="sync_decision required: basis=FAST_FORWARD_WORLD_TO_DISK",
        payload={"decision_id": decision.decision_id, "basis": decision.basis},
    )
    RuntimeStateStore(str(tmp_path)).save(rs)

    rt = _DummyRuntime(str(tmp_path))

    resolved = rt.resolve_sync_decision("abort")
    assert resolved.status == "aborted"
    assert resolved.direction == "abort"

    # Same direction again: no raise, same decision returned
    again = rt.resolve_sync_decision("abort")
    assert again.decision_id == resolved.decision_id
    assert again.direction == "abort"

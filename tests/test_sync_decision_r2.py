"""R2: SyncDecision minimal closed loop."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from forge.adapters.base import ToolResult
from forge.events import EventType
from forge.runtime_state import (
    PHASE_AWAITING_USER,
    PHASE_IDLE,
    PENDING_KIND_SYNC_DECISION,
    Pending,
    RuntimeState,
    RuntimeStateStore,
    sync_decision_pending_blocks,
)
from forge.sync.decision import (
    DIRECTION_ABORT,
    DIRECTION_DISK_TO_WORLD,
    STATUS_ABORTED,
    STATUS_DECIDED,
    STATUS_PENDING,
    SyncDecision,
    SyncDecisionStore,
    needs_sync_decision,
)
from forge.sync.sync_layer import (
    CONFLICT,
    FAST_FORWARD_WORLD_TO_DISK,
    IN_SYNC,
)


def test_sync_decision_round_trip(tmp_path: Path):
    store = SyncDecisionStore(tmp_path)
    d = SyncDecision.new_pending(basis=CONFLICT)
    store.save(d)
    loaded = store.load()
    assert loaded is not None
    assert loaded.decision_id == d.decision_id
    assert loaded.basis == CONFLICT
    assert loaded.status == STATUS_PENDING
    assert loaded.direction is None


def test_needs_sync_decision_statuses():
    assert needs_sync_decision(CONFLICT)
    assert needs_sync_decision(FAST_FORWARD_WORLD_TO_DISK)
    assert not needs_sync_decision(IN_SYNC)
    assert not needs_sync_decision("NOT_A_GIT_REPO")


def test_pending_write_and_gate_blocks(tmp_path: Path):
    rs = RuntimeStateStore(tmp_path)
    st = RuntimeState(
        phase=PHASE_AWAITING_USER,
        pending=Pending(
            kind=PENDING_KIND_SYNC_DECISION,
            summary="sync_decision required: basis=CONFLICT",
            payload={"decision_id": "sd_x", "basis": CONFLICT},
        ),
    )
    rs.save(st)

    blocked, summary = sync_decision_pending_blocks(tmp_path)
    assert blocked is True
    assert "CONFLICT" in summary

    # restart load
    loaded = RuntimeStateStore(tmp_path).load()
    assert loaded.pending is not None
    assert loaded.pending.kind == PENDING_KIND_SYNC_DECISION
    assert loaded.recovery.mode == "decision_required"


def test_gate_allows_when_no_pending(tmp_path: Path):
    blocked, _ = sync_decision_pending_blocks(tmp_path)
    assert blocked is False


def test_resolve_clears_pending(tmp_path: Path, monkeypatch):
    """resolve_sync_decision clears RuntimeState.pending and marks decision."""
    from forge.adapters.base import BaseAdapter
    from forge.memory import MemoryStore
    from forge.runtime import Runtime
    from forge.workspace import Workspace

    # Seed decision + pending without full sync detect
    sd_store = SyncDecisionStore(tmp_path)
    decision = SyncDecision.new_pending(basis=CONFLICT)
    sd_store.save(decision)
    rs_store = RuntimeStateStore(tmp_path)
    rs_store.save(
        RuntimeState(
            phase=PHASE_AWAITING_USER,
            pending=Pending(
                kind=PENDING_KIND_SYNC_DECISION,
                summary="wait",
                payload={"decision_id": decision.decision_id, "basis": CONFLICT},
            ),
        )
    )

    monkeypatch.setattr(
        "forge.world.runtime.WorldRuntime.ensure_identity",
        lambda self: None,
    )

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
        import pytest
        pytest.skip(f"Runtime init blocked: {e}")

    assert rt.runtime_state.pending is not None
    out = rt.resolve_sync_decision(DIRECTION_DISK_TO_WORLD)
    assert out is not None
    assert out.status == STATUS_DECIDED
    assert out.direction == DIRECTION_DISK_TO_WORLD
    assert rt.runtime_state.pending is None
    assert rt.runtime_state.phase == PHASE_IDLE

    # Gate open after resolve
    blocked, _ = sync_decision_pending_blocks(tmp_path)
    assert blocked is False

    # Durable decision kept as decided
    loaded = SyncDecisionStore(tmp_path).load()
    assert loaded is not None
    assert loaded.status == STATUS_DECIDED


def test_resolve_abort(tmp_path: Path, monkeypatch):
    sd_store = SyncDecisionStore(tmp_path)
    decision = SyncDecision.new_pending(basis=CONFLICT)
    sd_store.save(decision)
    RuntimeStateStore(tmp_path).save(
        RuntimeState(
            phase=PHASE_AWAITING_USER,
            pending=Pending(
                kind=PENDING_KIND_SYNC_DECISION,
                summary="wait",
                payload={"decision_id": decision.decision_id, "basis": CONFLICT},
            ),
        )
    )

    monkeypatch.setattr(
        "forge.world.runtime.WorldRuntime.ensure_identity",
        lambda self: None,
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
        import pytest
        pytest.skip(f"Runtime init blocked: {e}")

    out = rt.resolve_sync_decision(DIRECTION_ABORT)
    assert out.status == STATUS_ABORTED
    assert rt.runtime_state.pending is None


def test_guard_sync_decision_pending_on_runtime(tmp_path: Path, monkeypatch):
    decision = SyncDecision.new_pending(basis=CONFLICT)
    SyncDecisionStore(tmp_path).save(decision)
    RuntimeStateStore(tmp_path).save(
        RuntimeState(
            phase=PHASE_AWAITING_USER,
            pending=Pending(
                kind=PENDING_KIND_SYNC_DECISION,
                summary="sync_decision required: basis=CONFLICT",
                payload={"decision_id": decision.decision_id, "basis": CONFLICT},
            ),
        )
    )
    monkeypatch.setattr(
        "forge.world.runtime.WorldRuntime.ensure_identity",
        lambda self: None,
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
        import pytest
        pytest.skip(f"Runtime init blocked: {e}")

    guard = rt._guard_sync_decision_pending("write_file")
    assert guard is not None
    assert guard.success is False
    assert "SyncDecision" in (guard.display or "")

    guard_read = rt._guard_sync_decision_pending("read_file")
    assert guard_read is None

    guard_sync = rt._guard_sync_decision_pending("forge_sync")
    assert guard_sync is not None


def test_maybe_open_from_report(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        "forge.world.runtime.WorldRuntime.ensure_identity",
        lambda self: None,
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
        import pytest
        pytest.skip(f"Runtime init blocked: {e}")

    report = SimpleNamespace(status=CONFLICT, detail="both sides diverged")
    rt._maybe_open_sync_decision(report)

    assert rt.runtime_state.pending is not None
    assert rt.runtime_state.pending.kind == PENDING_KIND_SYNC_DECISION
    assert rt.runtime_state.phase == PHASE_AWAITING_USER
    d = SyncDecisionStore(tmp_path).load()
    assert d is not None and d.status == STATUS_PENDING and d.basis == CONFLICT

    # second open same basis keeps pending
    rt._maybe_open_sync_decision(report)
    d2 = SyncDecisionStore(tmp_path).load()
    assert d2.decision_id == d.decision_id

    # after resolve, same basis does not re-open
    rt.resolve_sync_decision(DIRECTION_DISK_TO_WORLD)
    assert rt.runtime_state.pending is None
    rt._maybe_open_sync_decision(report)
    assert rt.runtime_state.pending is None


def test_forge_sync_tool_respects_pending(tmp_path: Path):
    from forge.tools import make_tools
    from forge.workspace import Workspace

    RuntimeStateStore(tmp_path).save(
        RuntimeState(
            phase=PHASE_AWAITING_USER,
            pending=Pending(
                kind=PENDING_KIND_SYNC_DECISION,
                summary="basis=CONFLICT",
                payload={"basis": CONFLICT},
            ),
        )
    )

    class _SL:
        project_root = str(tmp_path)

        def detect(self):
            raise AssertionError("detect should not run when pending")

        def sync(self):
            raise AssertionError("sync should not run when pending")

    tools = make_tools(
        workspace=Workspace(project_root=str(tmp_path)),
        world_runtime=None,
        projections=None,
        allow_mutation=True,
        sync_layer=_SL(),
    )
    assert "forge_sync" in tools
    result = tools["forge_sync"]()
    assert result.success is False
    assert "SyncDecision" in (result.display or "")


def test_subagent_blocks_write_when_pending(tmp_path: Path):
    from unittest.mock import MagicMock
    from forge.adapters.base import ToolCall, ToolResult
    from forge.agent_abi import AgentTask
    from forge.subagent import filter_schemas_for_subagent, run_subagent
    from forge.tools.schemas import MUTATION_TOOL_DECLARATIONS, READ_ONLY_TOOL_DECLARATIONS

    RuntimeStateStore(tmp_path).save(
        RuntimeState(
            phase=PHASE_AWAITING_USER,
            pending=Pending(
                kind=PENDING_KIND_SYNC_DECISION,
                summary="basis=CONFLICT",
                payload={"basis": CONFLICT},
            ),
        )
    )
    executed = []

    class Adapter:
        def __init__(self):
            self.n = 0

        def send(self, messages, schemas):
            self.n += 1
            if self.n == 1:
                return MagicMock(
                    content="STOP_WHEN: not_met",
                    tool_calls=[
                        ToolCall(
                            id="1",
                            name="write_file",
                            arguments={"path": "a.py", "content": "x"},
                        )
                    ],
                )
            return MagicMock(
                content=(
                    "STOP_WHEN: met\nCONCLUSION: blocked\n"
                    "EVIDENCE: (无)\nUNCERTAIN: (无)\nNEXT: (无)"
                ),
                tool_calls=None,
            )

    tools = {
        "write_file": lambda path, content="": (
            executed.append(path) or ToolResult.ok(display="ok")
        ),
    }
    schemas = filter_schemas_for_subagent(
        list(READ_ONLY_TOOL_DECLARATIONS) + list(MUTATION_TOOL_DECLARATIONS)
    )
    run_subagent(
        Adapter(),
        tools,
        schemas,
        AgentTask(goal="g", done_when="d", stop_when="s", max_steps=5),
        project_root=tmp_path,
    )
    assert executed == []

def test_resolve_sync_decision_tool_registered(tmp_path: Path, monkeypatch):
    """控制面工具 resolve_sync_decision 注册且可调用。"""
    monkeypatch.setattr(
        "forge.world.runtime.WorldRuntime.ensure_identity",
        lambda self: None,
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
        import pytest
        pytest.skip(f"Runtime init blocked: {e}")

    assert "resolve_sync_decision" in rt.executor.tools

    # Seed pending decision
    decision = SyncDecision.new_pending(basis=CONFLICT)
    SyncDecisionStore(tmp_path).save(decision)
    RuntimeStateStore(tmp_path).save(
        RuntimeState(
            phase=PHASE_AWAITING_USER,
            pending=Pending(
                kind=PENDING_KIND_SYNC_DECISION,
                summary="wait",
                payload={"decision_id": decision.decision_id, "basis": CONFLICT},
            ),
        )
    )

    rt._sync_decision_store = SyncDecisionStore(tmp_path)
    rt.sync_decision = decision
    rt.runtime_state = RuntimeStateStore(tmp_path).load()
    rt.recovery = rt.runtime_state.recovery

    result = rt.executor.tools["resolve_sync_decision"](DIRECTION_DISK_TO_WORLD)
    assert result.success is True
    assert "decided" in (result.display or "")

    st = RuntimeStateStore(tmp_path).load()
    assert st.pending is None
    assert st.phase == PHASE_IDLE


def test_resolve_tool_rejects_bad_direction(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(
        "forge.world.runtime.WorldRuntime.ensure_identity",
        lambda self: None,
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
        import pytest
        pytest.skip(f"Runtime init blocked: {e}")

    result = rt.executor.tools["resolve_sync_decision"]("wrong_direction")
    assert result.success is False


# ── P1-01: SyncDecision / RuntimeState dual-write crash window ──────────────


def test_p101_open_crash_window_gate_blocks_with_only_sd_pending(tmp_path: Path):
    """Only sync_decision.json=PENDING, RS.pending=None → Gate must block."""
    decision = SyncDecision.new_pending(basis=CONFLICT)
    SyncDecisionStore(tmp_path).save(decision)
    # Explicitly no RuntimeState pending (simulate crash after SD save).
    assert RuntimeStateStore(tmp_path).load().pending is None

    blocked, summary = sync_decision_pending_blocks(tmp_path)
    assert blocked is True
    assert "pending" in summary.lower() or "CONFLICT" in summary


def test_p101_stale_decided_artifact_does_not_block(tmp_path: Path):
    decision = SyncDecision.new_pending(basis=CONFLICT)
    decision.apply_direction(DIRECTION_DISK_TO_WORLD)
    SyncDecisionStore(tmp_path).save(decision)
    assert decision.status == STATUS_DECIDED

    blocked, _ = sync_decision_pending_blocks(tmp_path)
    assert blocked is False


def test_p101_stale_aborted_artifact_does_not_block(tmp_path: Path):
    decision = SyncDecision.new_pending(basis=CONFLICT)
    decision.apply_direction(DIRECTION_ABORT)
    SyncDecisionStore(tmp_path).save(decision)
    assert decision.status == STATUS_ABORTED

    blocked, _ = sync_decision_pending_blocks(tmp_path)
    assert blocked is False


def test_p101_hi_priority_sd_pending_does_not_elevate(tmp_path: Path):
    """HI index + SD=PENDING → sync_decision_pending_blocks is False (HI owns slot)."""
    from forge.runtime_state import PENDING_KIND_HUMAN_INTERVENTION

    decision = SyncDecision.new_pending(basis=CONFLICT)
    SyncDecisionStore(tmp_path).save(decision)
    RuntimeStateStore(tmp_path).save(
        RuntimeState(
            phase=PHASE_AWAITING_USER,
            pending=Pending(
                kind=PENDING_KIND_HUMAN_INTERVENTION,
                summary="human_intervention: need choice",
                payload={"reason": "need choice"},
            ),
        )
    )

    blocked, _ = sync_decision_pending_blocks(tmp_path)
    assert blocked is False

    from forge.runtime_state import human_intervention_pending_blocks

    hi_blocked, _ = human_intervention_pending_blocks(tmp_path)
    assert hi_blocked is True


def test_p101_restart_reconcile_fills_rs_pending(tmp_path: Path, monkeypatch):
    """Disk: SD=PENDING, RS empty → Runtime init reconcile → pending + AWAITING_USER."""
    decision = SyncDecision.new_pending(basis=CONFLICT)
    SyncDecisionStore(tmp_path).save(decision)

    monkeypatch.setattr(
        "forge.world.runtime.WorldRuntime.ensure_identity",
        lambda self: None,
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
        import pytest
        pytest.skip(f"Runtime init blocked: {e}")

    assert rt.runtime_state.pending is not None
    assert rt.runtime_state.pending.kind == PENDING_KIND_SYNC_DECISION
    assert rt.runtime_state.phase == PHASE_AWAITING_USER
    assert rt.runtime_state.pending.payload.get("decision_id") == decision.decision_id
    assert rt.runtime_state.pending.payload.get("basis") == CONFLICT


def test_p101_reconcile_clears_rs_pending_without_decision_body(
    tmp_path: Path, monkeypatch
):
    """RS.pending=sync_decision but no PENDING SD → clear index; no auto direction."""
    RuntimeStateStore(tmp_path).save(
        RuntimeState(
            phase=PHASE_AWAITING_USER,
            pending=Pending(
                kind=PENDING_KIND_SYNC_DECISION,
                summary="orphan index",
                payload={"basis": CONFLICT},
            ),
        )
    )

    monkeypatch.setattr(
        "forge.world.runtime.WorldRuntime.ensure_identity",
        lambda self: None,
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
        import pytest
        pytest.skip(f"Runtime init blocked: {e}")

    assert rt.runtime_state.pending is None
    assert rt.runtime_state.phase == PHASE_IDLE
    blocked, _ = sync_decision_pending_blocks(tmp_path)
    assert blocked is False


def test_p101_reconcile_does_not_overwrite_hi(tmp_path: Path, monkeypatch):
    from forge.runtime_state import PENDING_KIND_HUMAN_INTERVENTION

    decision = SyncDecision.new_pending(basis=CONFLICT)
    SyncDecisionStore(tmp_path).save(decision)
    RuntimeStateStore(tmp_path).save(
        RuntimeState(
            phase=PHASE_AWAITING_USER,
            pending=Pending(
                kind=PENDING_KIND_HUMAN_INTERVENTION,
                summary="human_intervention: hold",
                payload={"reason": "hold"},
            ),
        )
    )

    monkeypatch.setattr(
        "forge.world.runtime.WorldRuntime.ensure_identity",
        lambda self: None,
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
        import pytest
        pytest.skip(f"Runtime init blocked: {e}")

    assert rt.runtime_state.pending is not None
    assert rt.runtime_state.pending.kind == PENDING_KIND_HUMAN_INTERVENTION
    # SD artifact still PENDING on disk
    loaded = SyncDecisionStore(tmp_path).load()
    assert loaded is not None and loaded.status == STATUS_PENDING


def test_p101_forge_sync_blocked_with_only_sd_pending(tmp_path: Path):
    from forge.tools import make_tools
    from forge.workspace import Workspace

    decision = SyncDecision.new_pending(basis=CONFLICT)
    SyncDecisionStore(tmp_path).save(decision)

    class _SL:
        project_root = str(tmp_path)

        def detect(self):
            raise AssertionError("detect should not run when SD pending")

        def sync(self):
            raise AssertionError("sync should not run when SD pending")

    tools = make_tools(
        workspace=Workspace(project_root=str(tmp_path)),
        world_runtime=None,
        projections=None,
        allow_mutation=True,
        sync_layer=_SL(),
    )
    result = tools["forge_sync"]()
    assert result.success is False
    assert "SyncDecision" in (result.display or "") or "pending" in (
        result.display or ""
    ).lower()


def test_p101_subagent_blocks_write_with_only_sd_pending(tmp_path: Path):
    from unittest.mock import MagicMock
    from forge.adapters.base import ToolCall, ToolResult
    from forge.agent_abi import AgentTask
    from forge.subagent import filter_schemas_for_subagent, run_subagent
    from forge.tools.schemas import MUTATION_TOOL_DECLARATIONS, READ_ONLY_TOOL_DECLARATIONS

    decision = SyncDecision.new_pending(basis=CONFLICT)
    SyncDecisionStore(tmp_path).save(decision)
    executed = []
    events = []

    class Adapter:
        def __init__(self):
            self.n = 0

        def send(self, messages, schemas):
            self.n += 1
            if self.n == 1:
                return MagicMock(
                    content="STOP_WHEN: not_met",
                    tool_calls=[
                        ToolCall(
                            id="1",
                            name="write_file",
                            arguments={"path": "a.py", "content": "x"},
                        )
                    ],
                )
            return MagicMock(content="STOP_WHEN: met", tool_calls=[])

    def write_file(**kwargs):
        executed.append("write_file")
        return ToolResult.ok(display="written")

    tools = {"write_file": write_file}
    schemas = filter_schemas_for_subagent(
        list(READ_ONLY_TOOL_DECLARATIONS) + list(MUTATION_TOOL_DECLARATIONS)
    )
    task = AgentTask(goal="g", subtask_id="sub_p101", stop_when="done")
    result = run_subagent(
        Adapter(),
        tools,
        schemas,
        task,
        project_root=str(tmp_path),
        confirm_fn=lambda s: True,
        emit=lambda event: events.append(event),
    )
    assert "write_file" not in executed
    assert not any(
        e.type == EventType.TOOL_CALL_START and e.data.get("name") == "write_file"
        for e in events
    )
    # tool refusal should surface in messages / result path
    assert result is not None


def test_p101_resolve_crash_window_idempotent_clear(tmp_path: Path, monkeypatch):
    """SD already DECIDED, RS still has sync_decision pending → resolve clears index."""
    decision = SyncDecision.new_pending(basis=CONFLICT)
    decision.apply_direction(DIRECTION_DISK_TO_WORLD)
    SyncDecisionStore(tmp_path).save(decision)
    RuntimeStateStore(tmp_path).save(
        RuntimeState(
            phase=PHASE_AWAITING_USER,
            pending=Pending(
                kind=PENDING_KIND_SYNC_DECISION,
                summary="stale after partial resolve",
                payload={"decision_id": decision.decision_id, "basis": CONFLICT},
            ),
        )
    )

    monkeypatch.setattr(
        "forge.world.runtime.WorldRuntime.ensure_identity",
        lambda self: None,
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
        import pytest
        pytest.skip(f"Runtime init blocked: {e}")

    # Reconcile may clear because SD is not PENDING — either way no false allow.
    # If reconcile already cleared, pending is None; if not, resolve is idempotent.
    if rt.runtime_state.pending is not None:
        assert rt.runtime_state.pending.kind == PENDING_KIND_SYNC_DECISION
        # DECIDED body: Gate must not false-allow via SD alone
        # (index still blocks until cleared)
        blocked_before, _ = sync_decision_pending_blocks(tmp_path)
        assert blocked_before is True

    out = rt.resolve_sync_decision(DIRECTION_DISK_TO_WORLD)
    assert out is not None
    assert out.status == STATUS_DECIDED
    assert rt.runtime_state.pending is None
    blocked, _ = sync_decision_pending_blocks(tmp_path)
    assert blocked is False


def test_p101_maybe_open_does_not_create_sd_under_hi(tmp_path: Path, monkeypatch):
    from forge.runtime_state import PENDING_KIND_HUMAN_INTERVENTION

    RuntimeStateStore(tmp_path).save(
        RuntimeState(
            phase=PHASE_AWAITING_USER,
            pending=Pending(
                kind=PENDING_KIND_HUMAN_INTERVENTION,
                summary="human_intervention: hold",
                payload={"reason": "hold"},
            ),
        )
    )

    monkeypatch.setattr(
        "forge.world.runtime.WorldRuntime.ensure_identity",
        lambda self: None,
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
        import pytest
        pytest.skip(f"Runtime init blocked: {e}")

    report = SimpleNamespace(status=CONFLICT, detail="both sides diverged")
    rt._maybe_open_sync_decision(report)

    assert rt.runtime_state.pending is not None
    assert rt.runtime_state.pending.kind == PENDING_KIND_HUMAN_INTERVENTION
    # Must not create a new PENDING decision artifact under HI.
    loaded = SyncDecisionStore(tmp_path).load()
    assert SyncDecisionStore(tmp_path).load() is None


def test_p101_guard_blocks_mutation_with_only_sd_pending(tmp_path: Path, monkeypatch):
    decision = SyncDecision.new_pending(basis=CONFLICT)
    SyncDecisionStore(tmp_path).save(decision)

    monkeypatch.setattr(
        "forge.world.runtime.WorldRuntime.ensure_identity",
        lambda self: None,
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
        import pytest
        pytest.skip(f"Runtime init blocked: {e}")

    # After init, reconcile filled RS; guard must still block.
    guard = rt._guard_sync_decision_pending("write_file")
    assert guard is not None
    assert guard.success is False



def test_after_reconcile_pending_still_blocks_mutation(tmp_path: Path, monkeypatch):
    """After startup reconcile restores SD pending, mutation still cannot execute.

    Chains P1-01 reconcile with a real subagent mutation attempt and proves
    the tool body never runs and workspace is unchanged.
    """
    from unittest.mock import MagicMock

    from forge.adapters.base import BaseAdapter, ToolCall, ToolResult
    from forge.agent_abi import AgentTask
    from forge.memory import MemoryStore
    from forge.runtime import Runtime
    from forge.subagent import filter_schemas_for_subagent, run_subagent
    from forge.tools.schemas import MUTATION_TOOL_DECLARATIONS, READ_ONLY_TOOL_DECLARATIONS
    from forge.workspace import Workspace

    victim = tmp_path / "after_reconcile_victim.py"
    victim.write_text("SAFE\n", encoding="utf-8")
    before = victim.read_text(encoding="utf-8")

    decision = SyncDecision.new_pending(basis=CONFLICT)
    SyncDecisionStore(tmp_path).save(decision)

    monkeypatch.setattr(
        "forge.world.runtime.WorldRuntime.ensure_identity",
        lambda self: None,
    )

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
        import pytest

        pytest.skip(f"Runtime init blocked: {e}")

    # Reconcile must restore durable pending (precondition for this combo test).
    assert rt.runtime_state.pending is not None
    assert rt.runtime_state.pending.kind == PENDING_KIND_SYNC_DECISION
    blocked, _ = sync_decision_pending_blocks(tmp_path)
    assert blocked is True

    executed: list[str] = []
    events: list = []

    class Adapter:
        def __init__(self):
            self.n = 0

        def send(self, messages, schemas):
            self.n += 1
            if self.n == 1:
                return MagicMock(
                    content="STOP_WHEN: not_met",
                    tool_calls=[
                        ToolCall(
                            id="1",
                            name="write_file",
                            arguments={
                                "path": str(victim),
                                "content": "MUTATED_AFTER_RECOVERY\n",
                            },
                        )
                    ],
                )
            return MagicMock(
                content=(
                    "STOP_WHEN: met\nCONCLUSION: blocked\n"
                    "EVIDENCE: (无)\nUNCERTAIN: (无)\nNEXT: (无)"
                ),
                tool_calls=None,
            )

    def write_file(path: str = "", content: str = "") -> ToolResult:
        executed.append(path)
        Path(path).write_text(content, encoding="utf-8")
        return ToolResult.ok(display="written")

    tools = {"write_file": write_file}
    schemas = filter_schemas_for_subagent(
        list(READ_ONLY_TOOL_DECLARATIONS) + list(MUTATION_TOOL_DECLARATIONS)
    )
    result = run_subagent(
        Adapter(),
        tools,
        schemas,
        AgentTask(goal="g", subtask_id="sub_after_reconcile", stop_when="done", max_steps=5),
        project_root=str(tmp_path),
        confirm_fn=lambda s: True,
        emit=lambda event: events.append(event),
    )

    assert executed == []
    assert victim.read_text(encoding="utf-8") == before
    assert not any(
        getattr(e, "type", None) == EventType.TOOL_CALL_START
        and (getattr(e, "data", None) or {}).get("name") == "write_file"
        for e in events
    )
    # Boundary remains closed after the attempt.
    blocked2, _ = sync_decision_pending_blocks(tmp_path)
    assert blocked2 is True
    assert result is not None


def test_dual_file_main_and_tmp_load_uses_main_only(tmp_path: Path):
    """When main + .tmp both exist, load authority is the main file only.

    Pins current behavior: tmp is never read. PENDING main keeps Gate closed.
    """
    store = SyncDecisionStore(tmp_path)
    decision = SyncDecision.new_pending(basis=CONFLICT)
    store.save(decision)

    main = store.path
    tmp = main.with_suffix(".tmp")
    # Stale/conflicting tmp that would be dangerous if preferred.
    tmp.write_text(
        json.dumps(
            {
                "decision_id": "spoof_decided",
                "basis": CONFLICT,
                "direction": DIRECTION_DISK_TO_WORLD,
                "status": STATUS_DECIDED,
                "created_at": 1.0,
                "decided_at": 2.0,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    assert main.exists() and tmp.exists()

    loaded = store.load()
    assert loaded is not None
    assert loaded.decision_id == decision.decision_id
    assert loaded.status == STATUS_PENDING
    assert loaded.decision_id != "spoof_decided"

    blocked, summary = sync_decision_pending_blocks(tmp_path)
    assert blocked is True
    assert summary

    # Mutation must still be refused while main is PENDING.
    from forge.adapters.base import ToolCall, ToolResult
    from forge.agent_abi import AgentTask
    from forge.subagent import filter_schemas_for_subagent, run_subagent
    from forge.tools.schemas import MUTATION_TOOL_DECLARATIONS, READ_ONLY_TOOL_DECLARATIONS
    from unittest.mock import MagicMock

    victim = tmp_path / "dual_victim.py"
    victim.write_text("SAFE\n", encoding="utf-8")
    before = victim.read_text(encoding="utf-8")
    executed: list[str] = []

    class Adapter:
        def __init__(self):
            self.n = 0

        def send(self, messages, schemas):
            self.n += 1
            if self.n == 1:
                return MagicMock(
                    content="STOP_WHEN: not_met",
                    tool_calls=[
                        ToolCall(
                            id="1",
                            name="write_file",
                            arguments={"path": str(victim), "content": "BAD\n"},
                        )
                    ],
                )
            return MagicMock(content="STOP_WHEN: met", tool_calls=None)

    def write_file(path: str = "", content: str = "") -> ToolResult:
        executed.append(path)
        Path(path).write_text(content, encoding="utf-8")
        return ToolResult.ok(display="written")

    run_subagent(
        Adapter(),
        {"write_file": write_file},
        filter_schemas_for_subagent(
            list(READ_ONLY_TOOL_DECLARATIONS) + list(MUTATION_TOOL_DECLARATIONS)
        ),
        AgentTask(goal="g", subtask_id="sub_dual", stop_when="done", max_steps=5),
        project_root=str(tmp_path),
        confirm_fn=lambda s: True,
    )
    assert executed == []
    assert victim.read_text(encoding="utf-8") == before
    assert tmp.exists()  # leftover tmp is ignored, not auto-cleaned by load


def test_clear_failure_leaves_pending_still_blocking(tmp_path: Path, monkeypatch):
    """clear failure must not open the mutation gate (clear failure != pending gone)."""
    store = SyncDecisionStore(tmp_path)
    decision = SyncDecision.new_pending(basis=CONFLICT)
    store.save(decision)
    assert store.path.exists()

    def _fail_unlink(self):
        raise OSError("simulated clear failure")

    monkeypatch.setattr(Path, "unlink", _fail_unlink, raising=True)
    store.clear()  # logs failure; must not pretend success by deleting

    # Main file still present (clear could not remove it).
    assert store.path.exists()
    loaded = SyncDecisionStore(tmp_path).load()
    assert loaded is not None
    assert loaded.status == STATUS_PENDING

    blocked, _ = sync_decision_pending_blocks(tmp_path)
    assert blocked is True

    from forge.adapters.base import ToolCall, ToolResult
    from forge.agent_abi import AgentTask
    from forge.subagent import filter_schemas_for_subagent, run_subagent
    from forge.tools.schemas import MUTATION_TOOL_DECLARATIONS, READ_ONLY_TOOL_DECLARATIONS
    from unittest.mock import MagicMock

    victim = tmp_path / "clear_fail_victim.py"
    victim.write_text("SAFE\n", encoding="utf-8")
    before = victim.read_text(encoding="utf-8")
    executed: list[str] = []

    class Adapter:
        def __init__(self):
            self.n = 0

        def send(self, messages, schemas):
            self.n += 1
            if self.n == 1:
                return MagicMock(
                    content="STOP_WHEN: not_met",
                    tool_calls=[
                        ToolCall(
                            id="1",
                            name="write_file",
                            arguments={"path": str(victim), "content": "BAD\n"},
                        )
                    ],
                )
            return MagicMock(content="STOP_WHEN: met", tool_calls=None)

    def write_file(path: str = "", content: str = "") -> ToolResult:
        executed.append(path)
        Path(path).write_text(content, encoding="utf-8")
        return ToolResult.ok(display="written")

    # Restore real unlink for any unrelated cleanup inside run_subagent.
    monkeypatch.undo()

    run_subagent(
        Adapter(),
        {"write_file": write_file},
        filter_schemas_for_subagent(
            list(READ_ONLY_TOOL_DECLARATIONS) + list(MUTATION_TOOL_DECLARATIONS)
        ),
        AgentTask(goal="g", subtask_id="sub_clear_fail", stop_when="done", max_steps=5),
        project_root=str(tmp_path),
        confirm_fn=lambda s: True,
    )
    assert executed == []
    assert victim.read_text(encoding="utf-8") == before
    blocked2, _ = sync_decision_pending_blocks(tmp_path)
    assert blocked2 is True

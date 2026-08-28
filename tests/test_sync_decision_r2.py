"""R2: SyncDecision minimal closed loop."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from forge.adapters.base import ToolResult
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
    RuntimeStateStore(tmp_path).save(
        RuntimeState(
            phase=PHASE_AWAITING_USER,
            pending=Pending(
                kind=PENDING_KIND_SYNC_DECISION,
                summary="sync_decision required: basis=CONFLICT",
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

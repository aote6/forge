"""Defense-in-depth: resolve_sync_decision_tool checks user hint.

Covers the parameter-inertia bug class: user says abort, model passes
world_to_disk. The hint is captured by dp.py only during AWAITING_USER +
sync_decision pending, then consumed (cleared) by the tool.
"""
import pytest

from forge.workspace import Workspace
from forge.memory import MemoryStore
from forge.runtime import Runtime


class _DummyAdapter:
    model_name = "dummy"
    def send(self, *a, **k):
        raise NotImplementedError


def _runtime(tmp_path):
    from forge.sync.decision import SyncDecision, SyncDecisionStore, STATUS_PENDING
    ws = Workspace(project_root=str(tmp_path))
    runtime = Runtime(_DummyAdapter(), ws, MemoryStore())

    # 构造一个待决议的 SyncDecision
    sd = SyncDecision(
        decision_id="sd_test",
        basis="FAST_FORWARD_DISK_TO_WORLD",
        direction="",
        status=STATUS_PENDING,
    )
    runtime._sync_decision_store.save(sd)
    runtime.sync_decision = sd

    # 构造 pending
    from forge.runtime_state import RuntimeState, PHASE_AWAITING_USER
    runtime.runtime_state = RuntimeState(phase=PHASE_AWAITING_USER)
    runtime.runtime_state.pending = type("P", (), {
        "kind": "sync_decision",
        "summary": "test",
        "payload": {"decision_id": "sd_test", "basis": "FAST_FORWARD_DISK_TO_WORLD"},
    })()
    return runtime


def _get_tool(runtime):
    for name, fn in runtime.executor.tools.items():
        if name == "resolve_sync_decision":
            return fn
    raise AssertionError("resolve_sync_decision tool not found")


def test_hint_mismatch_rejects_and_clears(tmp_path):
    runtime = _runtime(tmp_path)
    runtime._last_user_sync_choice_hint = "abort"
    tool = _get_tool(runtime)

    result = tool(direction="world_to_disk")

    assert result.success is False
    assert "abort" in result.display
    assert runtime._last_user_sync_choice_hint is None


def test_hint_match_allows(tmp_path):
    runtime = _runtime(tmp_path)
    runtime._last_user_sync_choice_hint = "abort"
    tool = _get_tool(runtime)

    result = tool(direction="abort")

    assert result.success is True
    assert runtime._last_user_sync_choice_hint is None


def test_no_hint_no_interception(tmp_path):
    runtime = _runtime(tmp_path)
    runtime._last_user_sync_choice_hint = None
    tool = _get_tool(runtime)

    result = tool(direction="world_to_disk")

    assert result.success is True

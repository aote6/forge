"""Pending Action Gate 与策略桶、Guard 正交性。"""
from __future__ import annotations

from forge.adapters.base import ToolResult
from forge.runtime import (
    PendingAction,
    Runtime,
    _write_strategy,
    _WRITE_CONFIRM_TOOLS,
    _WRITE_RECOVERY_TOOLS,
)


def test_write_strategy_buckets():
    assert _write_strategy("read_file") == "READ"
    assert _write_strategy("search_code") == "READ"
    assert _write_strategy("str_replace") == "WRITE_CONFIRM"
    assert _write_strategy("post_toot") == "WRITE_CONFIRM"
    assert _write_strategy("write_file") == "WRITE_CONFIRM"
    assert _write_strategy("forge_sync") == "WRITE_RECOVERY"
    assert _write_strategy("undo_last_tx") == "WRITE_RECOVERY"
    assert _write_strategy("submit_plan") == "READ"
    assert "forge_sync" in _WRITE_RECOVERY_TOOLS
    assert "str_replace" in _WRITE_CONFIRM_TOOLS
    assert "forge_sync" not in _WRITE_CONFIRM_TOOLS


def test_guard_still_blocks_after_confirm(tmp_path):
    """已确认也不能绕过 external-change 类 Guard（用 path_map degraded 模拟）。"""
    from forge.conversation import Conversation
    from forge.events import EventType
    from forge.workspace import Workspace

    class _World:
        def is_degraded(self, name):
            return name == "path_map"

    class _Ex:
        def execute(self, tc):
            raise AssertionError("must not execute when guard fails")

    rt = object.__new__(Runtime)
    rt.executor = _Ex()
    rt.world = _World()
    rt.sync_layer = None
    rt.workspace = Workspace(project_root=str(tmp_path))
    rt.conversation = Conversation()
    rt._handlers = {t: [] for t in EventType}
    rt._working_set = None
    rt._pending_action = PendingAction(
        tool="str_replace",
        args={"path": "a.py", "old_string": "a", "new_string": "b"},
        tool_call_id="x",
        summary="x",
    )
    out = rt._execute_pending_action()
    assert "path_map" in out or "DEGRADED" in out or "禁止" in out
    assert rt._pending_action is None

"""forge_sync：detect 只读观察；仅 FAST_FORWARD 进 Pending Action。

P1 note: main AI cannot call forge_sync / mutation tools at all — those are
refused by _main_tool_policy_denied before FORGE_SYNC / WRITE_CONFIRM paths.
PendingAction + confirm semantics remain for pre-set pending / execution-plane
recovery tests below.
"""
from __future__ import annotations

from types import SimpleNamespace

from forge.adapters.base import Message, ToolCall, ToolResult
from forge.conversation import Conversation
from forge.events import EventType
from forge.runtime import Runtime, ToolExecutor, PendingAction, _write_strategy
from forge.sync.sync_layer import (
    CONFLICT,
    FAST_FORWARD_DISK_TO_WORLD,
    FAST_FORWARD_WORLD_TO_DISK,
    IN_SYNC,
    NOT_A_GIT_REPO,
    WORLD_UNAVAILABLE,
    SyncReport,
)
from forge.workspace import Workspace


def _bare(**kwargs):
    rt = object.__new__(Runtime)
    rt.adapter = kwargs.get("adapter")
    rt.workspace = kwargs.get("workspace") or Workspace(project_root=str(kwargs.get("tmp", "/tmp")))
    rt.conversation = Conversation()
    rt.executor = kwargs.get("executor") or ToolExecutor({})
    rt._handlers = {t: [] for t in EventType}
    rt._pending_action = None
    rt._last_tool_calls = 0
    rt._last_assistant_replies = []
    rt.sync_layer = kwargs.get("sync_layer")
    rt.world = kwargs.get("world")
    rt._working_set = None
    return rt


class _FakeAdapter:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def send(self, messages, schemas):
        self.calls.append(schemas)
        return self._responses.pop(0) if self._responses else Message(role="assistant", content="")


class _DetectLayer:
    def __init__(self, report: SyncReport):
        self._report = report
        self.sync_calls = 0
        self.detect_calls = 0

    def detect(self):
        self.detect_calls += 1
        return self._report

    def sync(self):
        self.sync_calls += 1
        return self._report


def _report(status, **kw):
    return SyncReport(status=status, detail=kw.get("detail", status), **{k: v for k, v in kw.items() if k != "detail"})


def test_write_strategy_forge_sync_is_special():
    assert _write_strategy("forge_sync") == "FORGE_SYNC"
    assert _write_strategy("undo_last_tx") == "WRITE_RECOVERY"
    assert _write_strategy("str_replace") == "WRITE_CONFIRM"


def test_main_forge_sync_refused_by_policy_in_sync(tmp_path):
    """P1: main AI forge_sync → policy refuse; no detect, no pending, no executor."""
    layer = _DetectLayer(_report(IN_SYNC, detail="ok"))
    executed = []

    class _Ex:
        def execute(self, tc):
            executed.append(tc)
            return ToolResult.ok(display="should not run")

    adapter = _FakeAdapter([
        Message(
            role="assistant",
            content=None,
            tool_calls=[ToolCall(id="1", name="forge_sync", arguments={})],
        ),
        Message(role="assistant", content="已拒绝", tool_calls=None),
    ])
    rt = _bare(
        adapter=adapter,
        workspace=Workspace(project_root=str(tmp_path)),
        executor=_Ex(),
        sync_layer=layer,
        tmp=tmp_path,
    )
    rt._run_conversation("sync")
    assert rt._pending_action is None
    # detect may run once via _sync_system_hint; forge_sync must not advance
    assert layer.sync_calls == 0
    assert executed == []


def test_main_forge_sync_refused_by_policy_conflict(tmp_path):
    layer = _DetectLayer(_report(CONFLICT, detail="both sides changed"))
    executed = []

    class _Ex:
        def execute(self, tc):
            executed.append(tc)
            return ToolResult.ok(display="no")

    adapter = _FakeAdapter([
        Message(
            role="assistant",
            content=None,
            tool_calls=[ToolCall(id="1", name="forge_sync", arguments={})],
        ),
        Message(role="assistant", content="conflict noted", tool_calls=None),
    ])
    rt = _bare(
        adapter=adapter,
        workspace=Workspace(project_root=str(tmp_path)),
        executor=_Ex(),
        sync_layer=layer,
        tmp=tmp_path,
    )
    rt._run_conversation("sync")
    assert rt._pending_action is None
    # detect may run once via _sync_system_hint; forge_sync must not advance
    assert layer.sync_calls == 0
    assert executed == []


def test_main_forge_sync_refused_by_policy_fast_forward(tmp_path):
    """P1: even FAST_FORWARD does not open PendingAction for main forge_sync."""
    layer = _DetectLayer(_report(FAST_FORWARD_DISK_TO_WORLD, detail="disk ahead"))
    executed = []

    class _Ex:
        def execute(self, tc):
            executed.append(tc)
            return ToolResult.ok(display="no")

    adapter = _FakeAdapter([
        Message(
            role="assistant",
            content=None,
            tool_calls=[ToolCall(id="ff1", name="forge_sync", arguments={})],
        ),
        Message(role="assistant", content="refused", tool_calls=None),
    ])
    rt = _bare(
        adapter=adapter,
        workspace=Workspace(project_root=str(tmp_path)),
        executor=_Ex(),
        sync_layer=layer,
        tmp=tmp_path,
    )
    out = rt._run_conversation("sync")
    assert rt._pending_action is None
    # detect may run once via _sync_system_hint; forge_sync must not advance
    assert layer.sync_calls == 0
    assert executed == []


def test_main_forge_sync_refused_world_unavailable(tmp_path):
    layer = _DetectLayer(_report(WORLD_UNAVAILABLE, detail="veritasd down"))
    adapter = _FakeAdapter([
        Message(role="assistant", content=None, tool_calls=[ToolCall(id="1", name="forge_sync", arguments={})]),
        Message(role="assistant", content="world down", tool_calls=None),
    ])
    rt = _bare(adapter=adapter, workspace=Workspace(project_root=str(tmp_path)), sync_layer=layer, tmp=tmp_path)
    rt._run_conversation("sync")
    assert rt._pending_action is None
    # detect may run once via _sync_system_hint; forge_sync must not advance
    assert layer.sync_calls == 0


def test_main_str_replace_refused_by_policy(tmp_path):
    """P1: main mutation is policy-refused, not WRITE_CONFIRM PendingAction."""
    executed = []

    class _Ex:
        def execute(self, tc):
            executed.append(tc)
            return ToolResult.ok(display="no")

    adapter = _FakeAdapter([
        Message(
            role="assistant",
            content=None,
            tool_calls=[
                ToolCall(
                    id="w1",
                    name="str_replace",
                    arguments={"path": "a.py", "old_string": "a", "new_string": "b"},
                )
            ],
        ),
        Message(role="assistant", content="refused write", tool_calls=None),
    ])
    rt = _bare(
        adapter=adapter,
        workspace=Workspace(project_root=str(tmp_path)),
        executor=_Ex(),
        tmp=tmp_path,
    )
    rt.sync_layer = None
    out = rt._run_conversation("改文件")
    assert rt._pending_action is None
    assert executed == []


def test_full_schemas_control_plus_main_read_only(tmp_path):
    from forge.tools.schemas import CONTROL_PLANE_TOOLS, MAIN_READ_ONLY_TOOL_NAMES

    adapter = _FakeAdapter([Message(role="assistant", content="ok", tool_calls=None)])
    rt = _bare(adapter=adapter, workspace=Workspace(project_root=str(tmp_path)), tmp=tmp_path)
    rt.sync_layer = None
    rt._run_conversation("hi")
    names = {s["name"] for s in adapter.calls[0]}
    assert names == CONTROL_PLANE_TOOLS | MAIN_READ_ONLY_TOOL_NAMES
    assert "spawn_subagent" in names
    assert "verify_tool_call" in names
    assert "read_file" in names
    assert "forge_sync" not in names
    assert "str_replace" not in names
    assert "run_command" not in names


def test_guard_blocks_confirmed_pending_action(tmp_path):
    """Pre-set PendingAction still respects path_map degraded guard (execution path)."""
    class _World:
        def is_degraded(self, name):
            return name == "path_map"

    class _Ex:
        def execute(self, tc):
            raise AssertionError("must not execute")

    rt = _bare(
        workspace=Workspace(project_root=str(tmp_path)),
        executor=_Ex(),
        world=_World(),
        tmp=tmp_path,
    )
    rt.sync_layer = None
    rt._pending_action = PendingAction(
        tool="str_replace",
        args={"path": "a.py", "old_string": "x", "new_string": "y"},
        tool_call_id="g1",
        summary="x",
    )
    out = rt._execute_pending_action()
    assert "path_map" in out or "禁止" in out or "DEGRADED" in out
    assert rt._pending_action is None

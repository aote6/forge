"""forge_sync：detect 只读观察；仅 FAST_FORWARD 进 Pending Action。"""
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


def test_in_sync_no_pending(tmp_path):
    layer = _DetectLayer(_report(IN_SYNC, detail="ok"))
    adapter = _FakeAdapter([
        Message(
            role="assistant",
            content=None,
            tool_calls=[ToolCall(id="1", name="forge_sync", arguments={})],
        ),
        Message(role="assistant", content="已对齐", tool_calls=None),
    ])
    executed = []

    class _Ex:
        def execute(self, tc):
            executed.append(tc.name)
            return ToolResult.ok(display="should not run full tool")

    rt = _bare(adapter=adapter, workspace=Workspace(project_root=str(tmp_path)), executor=_Ex(), sync_layer=layer, tmp=tmp_path)
    out = rt._run_conversation("对账")
    assert rt._pending_action is None
    assert executed == []
    assert layer.detect_calls >= 1
    assert layer.sync_calls == 0
    assert "已对齐" in out or "IN_SYNC" in (rt._last_tool_display or "")


def test_conflict_no_write_no_pending(tmp_path):
    layer = _DetectLayer(_report(CONFLICT, detail="both changed", divergent_paths=["a.py"]))
    adapter = _FakeAdapter([
        Message(
            role="assistant",
            content=None,
            tool_calls=[ToolCall(id="1", name="forge_sync", arguments={})],
        ),
        Message(role="assistant", content="看到冲突了", tool_calls=None),
    ])
    executed = []

    class _Ex:
        def execute(self, tc):
            executed.append(tc.name)
            return ToolResult.ok(display="no")

    rt = _bare(adapter=adapter, workspace=Workspace(project_root=str(tmp_path)), executor=_Ex(), sync_layer=layer, tmp=tmp_path)
    rt._run_conversation("对账")
    assert rt._pending_action is None
    assert executed == []
    assert layer.sync_calls == 0


def test_fast_forward_enters_pending(tmp_path):
    layer = _DetectLayer(
        _report(FAST_FORWARD_WORLD_TO_DISK, detail="world ahead", world_advanced=True)
    )
    adapter = _FakeAdapter([
        Message(
            role="assistant",
            content="需要对齐",
            tool_calls=[ToolCall(id="ff1", name="forge_sync", arguments={})],
        ),
    ])
    executed = []

    class _Ex:
        def execute(self, tc):
            executed.append(tc)
            return ToolResult.ok(display="synced")

    rt = _bare(adapter=adapter, workspace=Workspace(project_root=str(tmp_path)), executor=_Ex(), sync_layer=layer, tmp=tmp_path)
    out = rt._run_conversation("同步")
    assert rt._pending_action is not None
    assert rt._pending_action.tool == "forge_sync"
    assert rt._pending_action.tool_call_id == "ff1"
    assert executed == []
    assert "确认" in out
    assert FAST_FORWARD_WORLD_TO_DISK in out or "推进" in out


def test_fast_forward_confirm_executes_once(tmp_path):
    layer = _DetectLayer(_report(FAST_FORWARD_DISK_TO_WORLD, detail="disk ahead"))
    executed = []

    class _Ex:
        def execute(self, tc):
            executed.append((tc.name, dict(tc.arguments)))
            return ToolResult.ok(display="FAST_FORWARD_DONE")

    adapter = _FakeAdapter([
        Message(role="assistant", content="推进完成，无需再写。", tool_calls=None),
    ])
    rt = _bare(adapter=adapter, workspace=Workspace(project_root=str(tmp_path)), executor=_Ex(), sync_layer=layer, tmp=tmp_path)
    rt._pending_action = PendingAction(
        tool="forge_sync",
        args={"_detect_status": FAST_FORWARD_DISK_TO_WORLD, "_detect_summary": "disk ahead"},
        tool_call_id="ff2",
        summary="forge_sync FF",
    )
    out = rt._handle_pending_reply("确认")
    assert len(executed) == 1
    assert executed[0][0] == "forge_sync"
    # 内部 _ 键不得传给工具
    assert executed[0][1] == {}
    assert rt._pending_action is None
    assert "FAST_FORWARD_DONE" in out or "forge_sync" in out


def test_after_confirm_next_write_needs_gate_again(tmp_path):
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
    ])
    rt = _bare(adapter=adapter, workspace=Workspace(project_root=str(tmp_path)), executor=ToolExecutor({}), tmp=tmp_path)
    rt.sync_layer = None
    out = rt._run_conversation("改文件")
    assert rt._pending_action is not None
    assert rt._pending_action.tool == "str_replace"
    assert "确认" in out


def test_full_schemas_still_visible(tmp_path):
    adapter = _FakeAdapter([Message(role="assistant", content="ok", tool_calls=None)])
    rt = _bare(adapter=adapter, workspace=Workspace(project_root=str(tmp_path)), tmp=tmp_path)
    rt.sync_layer = None
    rt._run_conversation("hi")
    names = {s["name"] for s in adapter.calls[0]}
    assert "spawn_subagent" in names
    assert "verify_tool_call" in names
    assert "forge_sync" not in names
    assert "str_replace" not in names


def test_guard_blocks_confirmed_forge_sync(tmp_path):
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


def test_world_unavailable_no_pending(tmp_path):
    layer = _DetectLayer(_report(WORLD_UNAVAILABLE, detail="veritasd down"))
    adapter = _FakeAdapter([
        Message(role="assistant", content=None, tool_calls=[ToolCall(id="1", name="forge_sync", arguments={})]),
        Message(role="assistant", content="world down", tool_calls=None),
    ])
    rt = _bare(adapter=adapter, workspace=Workspace(project_root=str(tmp_path)), sync_layer=layer, tmp=tmp_path)
    rt._run_conversation("sync")
    assert rt._pending_action is None
    assert layer.sync_calls == 0

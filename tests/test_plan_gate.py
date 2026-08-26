"""Continuous Conversation + Pending Action Gate.

旧 Planning→Execution 已废除。本文件验证：
- 完整工具表始终可见
- WRITE_CONFIRM 冻结 PendingAction，不立即执行
- 确认后 Runtime 执行快照，不重问模型
- 确认后不打开整表 mutation 权限
"""
from __future__ import annotations

from forge.adapters.base import Message, ToolCall, ToolResult
from forge.conversation import Conversation
from forge.runtime import Runtime, ToolExecutor, PendingAction, _default_tool_schemas
from forge.tools.schemas import (
    MUTATION_TOOL_DECLARATIONS,
    READ_ONLY_TOOL_DECLARATIONS,
    RECONCILIATION_TOOL_DECLARATIONS,
)
from forge.workspace import Workspace


def _bare_runtime() -> Runtime:
    return object.__new__(Runtime)


class _FakeAdapter:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[list[dict]] = []

    def send(self, messages, schemas):
        self.calls.append(schemas)
        return self._responses.pop(0) if self._responses else Message(role="assistant", content="")


def test_full_schemas_include_mutations_and_sync(tmp_path):
    """模型默认看到 str_replace / post_toot / forge_sync。"""
    adapter = _FakeAdapter([
        Message(role="assistant", content="只读回答即可。", tool_calls=None),
    ])
    rt = _bare_runtime()
    rt.adapter = adapter
    rt.workspace = Workspace(project_root=str(tmp_path))
    rt.conversation = Conversation()
    rt.executor = ToolExecutor({})
    rt._handlers = {t: [] for t in __import__("forge.events", fromlist=["EventType"]).EventType}
    rt._pending_action = None
    rt._last_tool_calls = 0
    rt._last_assistant_replies = []

    rt._run_conversation("看看这个函数")

    names = {s["name"] for s in adapter.calls[0]}
    assert "str_replace" in names
    assert "post_toot" in names
    assert "forge_sync" in names
    assert "read_file" in names


def test_write_confirm_freezes_pending_action(tmp_path):
    """str_replace 进入 PendingAction，不调用 executor。"""
    adapter = _FakeAdapter([
        Message(
            role="assistant",
            content="准备替换",
            tool_calls=[
                ToolCall(
                    id="tc1",
                    name="str_replace",
                    arguments={"path": "a.py", "old_string": "X", "new_string": "Y"},
                )
            ],
        ),
    ])
    executed = []

    class _Ex:
        def execute(self, tc):
            executed.append(tc)
            return ToolResult.ok(display="should not run")

    rt = _bare_runtime()
    rt.adapter = adapter
    rt.workspace = Workspace(project_root=str(tmp_path))
    rt.conversation = Conversation()
    rt.executor = _Ex()
    from forge.events import EventType
    rt._handlers = {t: [] for t in EventType}
    rt._pending_action = None
    rt._last_tool_calls = 0
    rt._last_assistant_replies = []

    out = rt._run_conversation("改 a.py")

    assert executed == []
    assert rt._pending_action is not None
    assert rt._pending_action.tool == "str_replace"
    assert rt._pending_action.args["path"] == "a.py"
    assert rt._pending_action.tool_call_id == "tc1"
    assert "确认" in out


def test_confirm_executes_frozen_snapshot(tmp_path):
    """用户确认后 Runtime 直接执行原始 tool+args，不重问模型生成 tool_call。"""
    executed = []

    class _Ex:
        def execute(self, tc):
            executed.append((tc.name, dict(tc.arguments), tc.id))
            return ToolResult.ok(display="REPLACED_OK")

    # After confirm, _execute_pending_action continues with _run_conversation;
    # provide one plain answer so the follow-up loop ends.
    adapter = _FakeAdapter([
        Message(role="assistant", content="已验证完成。", tool_calls=None),
    ])
    rt = _bare_runtime()
    rt.adapter = adapter
    rt.workspace = Workspace(project_root=str(tmp_path))
    rt.conversation = Conversation()
    rt.executor = _Ex()
    from forge.events import EventType
    rt._handlers = {t: [] for t in EventType}
    rt.sync_layer = None
    rt.world = None
    rt._working_set = None
    rt._pending_action = PendingAction(
        tool="str_replace",
        args={"path": "a.py", "old_string": "X", "new_string": "Y"},
        tool_call_id="tc1",
        summary="str_replace path=a.py",
        assistant_content="准备替换",
    )
    rt._last_tool_calls = 0
    rt._last_assistant_replies = []

    out = rt._handle_pending_reply("确认")

    assert len(executed) == 1
    assert executed[0][0] == "str_replace"
    assert executed[0][1]["old_string"] == "X"
    assert executed[0][2] == "tc1"
    assert rt._pending_action is None
    assert "REPLACED_OK" in out or "str_replace" in out


def test_confirm_does_not_grant_blanket_mutation(tmp_path):
    """一次 action 执行完后 pending 清空；下一次写仍需确认。"""
    adapter = _FakeAdapter([
        Message(
            role="assistant",
            content="第二步",
            tool_calls=[
                ToolCall(
                    id="tc2",
                    name="write_file",
                    arguments={"path": "b.py", "content": "z"},
                )
            ],
        ),
    ])
    class _Ex:
        def execute(self, tc):
            return ToolResult.ok(display="wrote")

    rt = _bare_runtime()
    rt.adapter = adapter
    rt.workspace = Workspace(project_root=str(tmp_path))
    rt.conversation = Conversation()
    rt.executor = _Ex()
    from forge.events import EventType
    rt._handlers = {t: [] for t in EventType}
    rt.sync_layer = None
    rt.world = None
    rt._working_set = None
    rt._pending_action = None
    rt._last_tool_calls = 0
    rt._last_assistant_replies = []

    out = rt._run_conversation("再写 b.py")
    assert rt._pending_action is not None
    assert rt._pending_action.tool == "write_file"
    assert "确认" in out


def test_cancel_clears_pending():
    rt = _bare_runtime()
    rt._pending_action = PendingAction(
        tool="post_toot", args={"text": "hi"}, tool_call_id="1", summary="post"
    )
    out = rt._handle_pending_reply("取消")
    assert "取消" in out
    assert rt._pending_action is None


def test_default_schemas_helper():
    names = {s["name"] for s in _default_tool_schemas()}
    for n in ("str_replace", "post_toot", "forge_sync", "read_file", "submit_plan"):
        assert n in names
    # mutations present
    assert {d["name"] for d in MUTATION_TOOL_DECLARATIONS} <= names

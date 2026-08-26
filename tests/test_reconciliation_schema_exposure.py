"""forge_sync 始终出现在默认完整工具 schema 中（无 Planning/Execution 裁剪）。"""
from __future__ import annotations

from forge.adapters.base import Message
from forge.conversation import Conversation
from forge.events import EventType
from forge.runtime import Runtime, ToolExecutor, _default_tool_schemas
from forge.workspace import Workspace


def test_forge_sync_in_default_schemas():
    names = {s["name"] for s in _default_tool_schemas()}
    assert "forge_sync" in names


def test_forge_sync_visible_in_conversation_loop(tmp_path):
    class _FakeAdapter:
        def __init__(self):
            self.calls = []

        def send(self, messages, schemas):
            self.calls.append(schemas)
            return Message(role="assistant", content="ok", tool_calls=None)

    adapter = _FakeAdapter()
    rt = object.__new__(Runtime)
    rt.adapter = adapter
    rt.workspace = Workspace(project_root=str(tmp_path))
    rt.conversation = Conversation()
    rt.executor = ToolExecutor({})
    rt._handlers = {t: [] for t in EventType}
    rt._pending_action = None
    rt._last_tool_calls = 0
    rt._last_assistant_replies = []
    rt._run_conversation("status")
    names = {s["name"] for s in adapter.calls[0]}
    assert "forge_sync" in names, f"缺少 forge_sync，实际: {sorted(names)}"

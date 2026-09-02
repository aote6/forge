"""forge_sync 属于执行面；主 AI 默认 schema 为控制面（Phase 1 isolation）。"""
from __future__ import annotations

from forge.adapters.base import Message
from forge.conversation import Conversation
from forge.events import EventType
from forge.runtime import Runtime, ToolExecutor, _default_tool_schemas
from forge.tools.schemas import CONTROL_PLANE_TOOLS, EXECUTION_PLANE_TOOLS, MAIN_READ_ONLY_TOOL_NAMES
from forge.workspace import Workspace


def test_forge_sync_on_execution_plane_not_control_default():
    default_names = {s["name"] for s in _default_tool_schemas()}
    assert "forge_sync" not in default_names
    assert default_names == CONTROL_PLANE_TOOLS | MAIN_READ_ONLY_TOOL_NAMES
    assert "read_file" in default_names
    assert "forge_sync" in EXECUTION_PLANE_TOOLS


def test_conversation_loop_uses_control_plane_schemas(tmp_path):
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
    assert names == CONTROL_PLANE_TOOLS | MAIN_READ_ONLY_TOOL_NAMES
    assert "forge_sync" not in names
    assert "read_file" in names

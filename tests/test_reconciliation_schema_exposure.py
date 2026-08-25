#!/usr/bin/env python3
"""回归测试：forge_sync 必须同时出现在 Planning 和 Execution 两阶段
实际发给 adapter 的 schemas 里，而不只是 schemas.py 的静态声明里。"""
import sys
import types

from forge.workspace import Workspace
from forge.memory import MemoryStore
from forge.runtime import Runtime


class RecordingAdapter:
    """假 adapter：记录每次 send/send_stream 收到的 schemas，
    第一次调用就返回「无 tool_calls」的空响应，让 _run_conversation
    立刻退出循环，不需要真的驱动一整轮工具调用。"""

    def __init__(self):
        self.model_name = "recording-stub"
        self.captured_schemas = []

    def _fake_response(self):
        return types.SimpleNamespace(content="stub", tool_calls=None)

    def send(self, messages, schemas):
        self.captured_schemas.append(list(schemas))
        return self._fake_response()

    def send_stream(self, messages, schemas, on_text_delta=None):
        self.captured_schemas.append(list(schemas))
        return self._fake_response()


def _make_runtime(tmp_path):
    ws = Workspace(project_root=str(tmp_path))
    mem = MemoryStore()
    adapter = RecordingAdapter()
    rt = Runtime(adapter, ws, mem)
    return rt, adapter


def test_forge_sync_visible_in_planning(tmp_path):
    rt, adapter = _make_runtime(tmp_path)
    rt._run_planning("随便什么任务，只是为了触发一次 send")
    assert adapter.captured_schemas, "adapter.send 从未被调用，测试装配有问题"
    names = {s["name"] for s in adapter.captured_schemas[0]}
    assert "forge_sync" in names, f"Planning 阶段 schemas 缺少 forge_sync，实际: {sorted(names)}"


def test_forge_sync_visible_in_execution(tmp_path):
    rt, adapter = _make_runtime(tmp_path)
    rt._run_execution("随便什么任务", plan="随便什么计划")
    assert adapter.captured_schemas, "adapter.send 从未被调用，测试装配有问题"
    names = {s["name"] for s in adapter.captured_schemas[0]}
    assert "forge_sync" in names, f"Execution 阶段 schemas 缺少 forge_sync，实际: {sorted(names)}"

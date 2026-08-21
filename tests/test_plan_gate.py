"""规划→确认→执行 闸门测试。

验证：要改代码时先 submit_plan，运行时交还用户确认，确认后才放行 mutation。
用裸 Runtime（object.__new__ 绕过 __init__）+ FakeAdapter，不依赖 veritasd/网络。
"""
from __future__ import annotations

from forge.adapters.base import Message, ToolCall
from forge.conversation import Conversation
from forge.runtime import Runtime, ToolExecutor
from forge.tools.schemas import (
    MUTATION_TOOL_DECLARATIONS,
    READ_ONLY_TOOL_DECLARATIONS,
    SUBMIT_PLAN_DECLARATION,
)
from forge.workspace import Workspace


def _bare_runtime() -> Runtime:
    """构造一个绕过 __init__ 的 Runtime，只设测试需要的最小状态。"""
    return object.__new__(Runtime)


class _FakeAdapter:
    """按预设的响应序列应答 send()，并记录每次收到的 schemas。"""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[list[dict]] = []

    def send(self, messages, schemas):
        self.calls.append(schemas)
        return self._responses.pop(0) if self._responses else Message(role="assistant", content="")


def test_planning_pass_detects_submit_plan(tmp_path):
    """规划阶段模型调 submit_plan → 运行时中断并返回计划，记入 _submitted_plan。"""
    adapter = _FakeAdapter([
        Message(
            role="assistant",
            content="我打算改 a.py 的解析逻辑",
            tool_calls=[ToolCall(id="1", name="submit_plan", arguments={"plan": "改 a.py：把 X 换成 Y"})],
        ),
    ])
    rt = _bare_runtime()
    rt.adapter = adapter
    rt.workspace = Workspace(project_root=str(tmp_path))
    rt.conversation = Conversation()
    rt.executor = ToolExecutor({})
    rt._handlers = {}
    rt._submitted_plan = None
    rt._last_tool_calls = 0
    rt._last_assistant_replies = []

    out = rt._run_conversation(
        "修 bug",
        schemas=list(READ_ONLY_TOOL_DECLARATIONS) + [SUBMIT_PLAN_DECLARATION],
    )

    assert out == "改 a.py：把 X 换成 Y"
    assert rt._submitted_plan == "改 a.py：把 X 换成 Y"
    # 规划阶段绝不能出现 mutation 工具
    sent_schemas = adapter.calls[0]
    names = {s["name"] for s in sent_schemas}
    assert "submit_plan" in names
    assert not (names & {d["name"] for d in MUTATION_TOOL_DECLARATIONS})


def test_planning_pass_plain_answer_no_plan(tmp_path):
    """纯问答不调 submit_plan → 直接返回答案，不进入待确认状态。"""
    adapter = _FakeAdapter([
        Message(role="assistant", content="这个函数就是把字符串反转。", tool_calls=None),
    ])
    rt = _bare_runtime()
    rt.adapter = adapter
    rt.workspace = Workspace(project_root=str(tmp_path))
    rt.conversation = Conversation()
    rt.executor = ToolExecutor({})
    rt._handlers = {}
    rt._submitted_plan = None
    rt._last_tool_calls = 0
    rt._last_assistant_replies = []

    out = rt._run_conversation(
        "这个函数干嘛的",
        schemas=list(READ_ONLY_TOOL_DECLARATIONS) + [SUBMIT_PLAN_DECLARATION],
    )

    assert "反转" in out
    assert rt._submitted_plan is None


def test_gate_confirm_executes():
    rt = _bare_runtime()
    rt._pending_plan = "改 a.py"
    rt._pending_task = "修 bug"
    executed = {}
    rt._run_execution = lambda task, plan: executed.update(task=task, plan=plan) or "EXECUTED"

    out = rt._handle_plan_reply("确认")

    assert executed == {"task": "修 bug", "plan": "改 a.py"}
    assert out == "EXECUTED"
    assert rt._pending_plan is None
    assert rt._pending_task is None


def test_gate_confirm_with_extra_preserves_note():
    rt = _bare_runtime()
    rt._pending_plan = "改 a.py"
    rt._pending_task = "修 bug"
    executed = {}
    rt._run_execution = lambda task, plan: executed.update(task=task, plan=plan) or "EXECUTED"

    out = rt._handle_plan_reply("确认，另外记得跑测试")

    assert executed["plan"] == "改 a.py"
    assert "修 bug" in executed["task"]
    assert "记得跑测试" in executed["task"]  # 补充意见不能丢
    assert out == "EXECUTED"


def test_gate_cancel_aborts():
    rt = _bare_runtime()
    rt._pending_plan = "改 a.py"
    rt._pending_task = "修 bug"

    out = rt._handle_plan_reply("取消")

    assert "取消" in out
    assert rt._pending_plan is None


def test_gate_revision_replans_with_original_task():
    rt = _bare_runtime()
    rt._pending_plan = "改 a.py"
    rt._pending_task = "修 bug"
    replanned = {}
    rt._run_planning = lambda task: replanned.update(task=task) or "REPLANNED"

    out = rt._handle_plan_reply("改成用方案 B")

    assert out == "REPLANNED"
    assert "修 bug" in replanned["task"]
    assert "方案 B" in replanned["task"]
    assert rt._pending_plan is None

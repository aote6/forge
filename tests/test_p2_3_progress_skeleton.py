"""P2-3 契约测试：弱模型 / 免费模型长任务骨架。

目标（不要扩大）：
让弱模型在长工具循环中持续记住「现在要完成什么、已经完成什么、下一步是什么、
有什么风险」，减少重复搜索、失忆和无意义工具调用。

手段只有一个：把现有 WorkingSet 的信息压成短 checkpoint，作为**瞬时** system
注入交给下一轮模型；不新建状态系统、不新增工具、不改 mutation 语义。

用例映射：
  1. 每 5 个工具循环步骤出现一次 [PROGRESS]
  2. checkpoint 按周期持续出现（不是只出现一次）
  3. mutation 成功 → 下一轮上下文出现 [PROGRESS]
  4. mutation 失败 → 失败操作不进 done
  5. Working Set 的 goal/files_edited/pending_verify/open_hypotheses 进入 checkpoint
  6. 空 Working Set 不崩溃，用「无」兜底
  7. 接近 MAX_AGENT_STEPS → [FINAL CHECKPOINT]
  8. 少于 5 步的短任务：原有 tool calling 行为不变，不注入 checkpoint
  9. P2-2 不回归：首轮 sync system hint 仍在，不被 checkpoint 覆盖
 10. checkpoint 只是上下文提示，不产生新的工具 / 不进持久 conversation
"""
from __future__ import annotations

from types import SimpleNamespace

from forge.adapters.base import Message, ToolCall, ToolResult
from forge.conversation import Conversation
from forge.runtime import (
    MAX_AGENT_STEPS,
    PROGRESS_CHECKPOINT_EVERY,
    FINAL_CHECKPOINT_TAIL_STEPS,
    Runtime,
    WorkingSet,
    _final_checkpoint_text,
    _progress_checkpoint_text,
)
from forge.sync.sync_layer import CONFLICT, IN_SYNC, SyncReport
from forge.workspace import Workspace

_PROGRESS = "[PROGRESS]"
_FINAL = "[FINAL CHECKPOINT]"


# --------------------------------------------------------------------------- #
# 测试基础设施（复用 test_plan_gate / test_p2_2 的裸 Runtime + FakeAdapter 套路）
# --------------------------------------------------------------------------- #


class _RecordingAdapter:
    """记录每一轮 send() 收到的 messages / schemas，并按脚本应答。

    脚本用尽后：无限重复最后一个响应（用于跑满 MAX_AGENT_STEPS）。
    """

    def __init__(self, responses, repeat_last: bool = False):
        self._responses = list(responses)
        self.repeat_last = repeat_last
        self.rounds: list[list] = []
        self.schemas_seen: list[list] = []

    def send(self, messages, schemas):
        self.rounds.append(list(messages))
        self.schemas_seen.append(list(schemas or []))
        if self._responses:
            if self.repeat_last and len(self._responses) == 1:
                return self._responses[0]
            return self._responses.pop(0)
        return Message(role="assistant", content="done")


class _ScriptedExecutor:
    """最小 ToolExecutor 替身：按工具名返回预设 ToolResult，并记录调用。"""

    def __init__(self, results: dict | None = None, default: ToolResult | None = None):
        self.tools: dict = {}
        self._results = results or {}
        self._default = default or ToolResult.ok(display="RESULT: ok", payload={})
        self.calls: list[str] = []

    def execute(self, tool_call):
        self.calls.append(tool_call.name)
        return self._results.get(tool_call.name, self._default)


def _read_round(step: int) -> Message:
    return Message(
        role="assistant",
        content=None,
        tool_calls=[ToolCall(id=f"t{step}", name="read_file", arguments={"path": f"pkg/f{step}.py"})],
    )


def _runtime(tmp_path, adapter, executor, report=None) -> Runtime:
    rt = object.__new__(Runtime)
    rt.adapter = adapter
    rt.workspace = Workspace(project_root=str(tmp_path))
    rt.conversation = Conversation()
    rt.executor = executor
    rt._handlers = {}
    rt._submitted_plan = None
    rt._last_tool_calls = 0
    rt._last_assistant_replies = []
    rt.sync_layer = SimpleNamespace(
        detect=lambda: (report if report is not None else SyncReport(status=IN_SYNC)),
        world_available=lambda: True,
        external_change_detected=lambda: False,
        disk_change_detected=lambda: False,
    )
    return rt


def _system_texts(round_messages) -> list[str]:
    return [
        getattr(m, "content", "") or ""
        for m in round_messages
        if getattr(m, "role", None) == "system"
    ]


def _rounds_with(adapter, marker: str) -> list[int]:
    return [
        i
        for i, msgs in enumerate(adapter.rounds)
        if any(t.startswith(marker) for t in _system_texts(msgs))
    ]


def _checkpoint_text(adapter, round_i: int, marker: str) -> str:
    for t in _system_texts(adapter.rounds[round_i]):
        if t.startswith(marker):
            return t
    return ""


# --------------------------------------------------------------------------- #
# 1 / 2. 周期性 Progress checkpoint
# --------------------------------------------------------------------------- #


def test_progress_checkpoint_appears_after_five_steps(tmp_path):
    """超过 5 个工具循环步骤 → 上下文出现强制 [PROGRESS] checkpoint。"""
    adapter = _RecordingAdapter([_read_round(i) for i in range(8)])
    rt = _runtime(tmp_path, adapter, _ScriptedExecutor())

    rt._run_conversation("修 pkg 的解析 bug", schemas=[])

    hit = _rounds_with(adapter, _PROGRESS)
    assert hit, "长工具循环里必须出现 [PROGRESS] checkpoint"
    assert hit[0] == PROGRESS_CHECKPOINT_EVERY  # 前 5 步不打扰
    text = _checkpoint_text(adapter, hit[0], _PROGRESS)
    for field in ("goal:", "done:", "next:", "risk:"):
        assert field in text
    # 强制 checkpoint，不是普通建议
    assert "在继续任何工具调用之前" in text


def test_progress_checkpoint_repeats_periodically(tmp_path):
    """10+ 步的长循环里 checkpoint 必须按周期反复出现，不是只出现一次。"""
    adapter = _RecordingAdapter([_read_round(i) for i in range(14)])
    rt = _runtime(tmp_path, adapter, _ScriptedExecutor())

    rt._run_conversation("长任务", schemas=[])

    hit = _rounds_with(adapter, _PROGRESS)
    assert len(hit) >= 2, f"checkpoint 应周期出现，实际只在 {hit}"
    assert hit[:2] == [PROGRESS_CHECKPOINT_EVERY, PROGRESS_CHECKPOINT_EVERY * 2]


def test_progress_checkpoint_is_transient_not_accumulated(tmp_path):
    """瞬时注入：任一轮最多一条 [PROGRESS]，不无限追加污染上下文。"""
    adapter = _RecordingAdapter([_read_round(i) for i in range(14)])
    rt = _runtime(tmp_path, adapter, _ScriptedExecutor())

    rt._run_conversation("长任务", schemas=[])

    for msgs in adapter.rounds:
        n = sum(1 for t in _system_texts(msgs) if t.startswith(_PROGRESS))
        assert n <= 1


# --------------------------------------------------------------------------- #
# 3 / 4. mutation 成功 / 失败
# --------------------------------------------------------------------------- #


def _mutation_round(step: int) -> Message:
    return Message(
        role="assistant",
        content=None,
        tool_calls=[
            ToolCall(
                id=f"m{step}",
                name="str_replace",
                arguments={"path": "pkg/a.py", "old_string": "x", "new_string": "y"},
            )
        ],
    )


def _named_mutation_round(step: int, name: str) -> Message:
    return Message(
        role="assistant",
        content=None,
        tool_calls=[ToolCall(id=f"n{step}", name=name, arguments={})],
    )


def test_successful_mutation_triggers_checkpoint_next_round(tmp_path, monkeypatch):
    """mutation 成功 → 下一轮模型上下文包含 [PROGRESS]（不必等到第 5 步）。

    Pending Action Gate 下普通写会冻结；本测试清空 WRITE_CONFIRM 桶以隔离
    WorkingSet/checkpoint 行为（门禁由 test_plan_gate 覆盖）。
    """
    import forge.runtime as rtmod
    monkeypatch.setattr(rtmod, "_WRITE_CONFIRM_TOOLS", frozenset())
    ok = ToolResult.ok(
        display="RESULT: path=pkg/a.py replacements=1 tx=42 version=3",
        payload={"path": "pkg/a.py", "tx_id": 42},
    )
    adapter = _RecordingAdapter([_mutation_round(0), _read_round(1), _read_round(2)])
    rt = _runtime(tmp_path, adapter, _ScriptedExecutor({"str_replace": ok}))

    rt._run_conversation("改 pkg/a.py", schemas=[])

    assert 1 in _rounds_with(adapter, _PROGRESS)
    text = _checkpoint_text(adapter, 1, _PROGRESS)
    assert "pkg/a.py" in text  # 成功编辑进入 done


def test_failed_mutation_not_recorded_as_done(tmp_path, monkeypatch):
    """失败的 mutation 不得被记录为已完成。"""
    import forge.runtime as rtmod
    monkeypatch.setattr(rtmod, "_WRITE_CONFIRM_TOOLS", frozenset())
    bad = ToolResult.fail(
        display="str_replace failed: old_string not found\npath: pkg/a.py",
        payload={"path": "pkg/a.py"},
    )
    adapter = _RecordingAdapter([_mutation_round(0)] + [_read_round(i) for i in range(1, 8)])
    rt = _runtime(tmp_path, adapter, _ScriptedExecutor({"str_replace": bad}))

    rt._run_conversation("改 pkg/a.py", schemas=[])

    hit = _rounds_with(adapter, _PROGRESS)
    assert hit, "长循环仍应有周期 checkpoint"
    for i in hit:
        done_line = [
            ln
            for ln in _checkpoint_text(adapter, i, _PROGRESS).splitlines()
            if ln.startswith("done:")
        ]
        assert done_line
        assert "pkg/a.py" not in done_line[0], "失败的编辑不能出现在 done"


def test_failed_mutation_does_not_trigger_immediate_checkpoint(tmp_path, monkeypatch):
    """只有成功的 mutation 才触发额外 checkpoint。"""
    import forge.runtime as rtmod
    monkeypatch.setattr(rtmod, "_WRITE_CONFIRM_TOOLS", frozenset())
    bad = ToolResult.fail(display="str_replace failed", payload={})
    adapter = _RecordingAdapter([_mutation_round(0), _read_round(1), _read_round(2)])
    rt = _runtime(tmp_path, adapter, _ScriptedExecutor({"str_replace": bad}))

    rt._run_conversation("改 pkg/a.py", schemas=[])

    assert _rounds_with(adapter, _PROGRESS) == []


def test_forge_sync_success_does_not_trigger_checkpoint(tmp_path):
    """forge_sync 成功是对账不是文件编辑，不应触发 [PROGRESS] checkpoint。"""
    ok = ToolResult.ok(display="sync_status: IN_SYNC", payload={"mutation": True})
    adapter = _RecordingAdapter(
        [_named_mutation_round(0, "forge_sync"), _read_round(1), _read_round(2)]
    )
    rt = _runtime(tmp_path, adapter, _ScriptedExecutor({"forge_sync": ok}))

    rt._run_conversation("对账", schemas=[])

    assert _rounds_with(adapter, _PROGRESS) == []


def test_undo_last_tx_success_does_not_trigger_checkpoint(tmp_path):
    """undo_last_tx 成功是回滚不是文件编辑，不应触发 [PROGRESS] checkpoint。"""
    ok = ToolResult.ok(display="RESULT: undone", payload={})
    adapter = _RecordingAdapter(
        [_named_mutation_round(0, "undo_last_tx"), _read_round(1), _read_round(2)]
    )
    rt = _runtime(tmp_path, adapter, _ScriptedExecutor({"undo_last_tx": ok}))

    rt._run_conversation("撤销", schemas=[])

    assert _rounds_with(adapter, _PROGRESS) == []


# --------------------------------------------------------------------------- #
# 5 / 6. Working Set 联动 + 空状态兜底（纯函数）
# --------------------------------------------------------------------------- #


def test_checkpoint_uses_working_set_fields():
    ws = WorkingSet(goal="修复 pkg/a.py 的解析分支")
    ws.files_edited = ["pkg/a.py", "pkg/b.py"]
    ws.pending_verify = ["verify edit on pkg/b.py"]
    ws.open_hypotheses = ["str_replace failed on pkg/c.py"]

    text = _progress_checkpoint_text(ws)

    assert text.startswith(_PROGRESS)
    assert "修复 pkg/a.py 的解析分支" in text
    assert "pkg/a.py" in text and "pkg/b.py" in text
    assert "pkg/b.py" in text  # pending_verify → next
    assert "pkg/c.py" in text  # open_hypotheses → risk


def test_checkpoint_next_prefers_verify_target():
    ws = WorkingSet(goal="g")
    ws.files_edited = ["pkg/a.py"]
    ws.pending_verify = ["verify edit on pkg/a.py"]
    ws.verify_targets = ["tests/test_a.py"]

    text = _progress_checkpoint_text(ws)
    next_line = [ln for ln in text.splitlines() if ln.startswith("next:")][0]
    assert "run_test_structured" in next_line
    assert "tests/test_a.py" in next_line


def test_checkpoint_done_capped_at_three_items():
    ws = WorkingSet(goal="g")
    ws.files_edited = [f"pkg/f{i}.py" for i in range(9)]
    text = _progress_checkpoint_text(ws)
    done_line = [ln for ln in text.splitlines() if ln.startswith("done:")][0]
    assert done_line.count("pkg/f") == 3


def test_empty_working_set_checkpoint_is_safe():
    """空 Working Set 不崩溃，用「无」兜底，不编造信息。"""
    text = _progress_checkpoint_text(WorkingSet())

    assert text.startswith(_PROGRESS)
    for field in ("goal:", "done:", "next:", "risk:"):
        line = [ln for ln in text.splitlines() if ln.startswith(field)][0]
        assert line.split(":", 1)[1].strip() == "无"


def test_checkpoint_stays_short():
    """checkpoint 自己不能变成上下文污染源。"""
    ws = WorkingSet(goal="G" * 2000)
    ws.files_edited = [f"pkg/f{i}.py" for i in range(50)]
    ws.pending_verify = [f"verify edit on pkg/f{i}.py" for i in range(50)]
    ws.open_hypotheses = ["H" * 2000]

    text = _progress_checkpoint_text(ws)
    assert len(text.splitlines()) <= 10
    assert len(text) <= 1200


# --------------------------------------------------------------------------- #
# 7. 接近 MAX_AGENT_STEPS 的收束
# --------------------------------------------------------------------------- #


def test_final_checkpoint_text_shape():
    ws = WorkingSet(goal="收尾任务")
    ws.files_edited = ["pkg/a.py"]
    ws.pending_verify = ["verify edit on pkg/a.py"]

    text = _final_checkpoint_text(ws)

    assert text.startswith(_FINAL)
    for field in ("goal:", "done:", "unfinished:", "next:"):
        assert field in text
    assert "最大工具调用次数" in text
    assert "停止新的无关探索" in text
    assert "不要继续调用工具" in text


def test_final_checkpoint_near_max_steps(tmp_path):
    """跑满 MAX_AGENT_STEPS → 最后几步注入 [FINAL CHECKPOINT] 收束。"""
    adapter = _RecordingAdapter([_read_round(0)], repeat_last=True)
    rt = _runtime(tmp_path, adapter, _ScriptedExecutor())

    out = rt._run_conversation("永不停止的探索", schemas=[])

    assert out == "(达到最大工具调用次数)"
    assert len(adapter.rounds) == MAX_AGENT_STEPS
    final_hit = _rounds_with(adapter, _FINAL)
    assert final_hit == list(
        range(MAX_AGENT_STEPS - FINAL_CHECKPOINT_TAIL_STEPS, MAX_AGENT_STEPS)
    )
    # 收束阶段用 FINAL 取代周期 PROGRESS，不同时注入两份
    for i in final_hit:
        assert not any(t.startswith(_PROGRESS) for t in _system_texts(adapter.rounds[i]))


# --------------------------------------------------------------------------- #
# 8. 短任务不受影响
# --------------------------------------------------------------------------- #


def test_short_task_unaffected(tmp_path):
    """少于 5 步的普通任务：无 checkpoint，原有 tool calling 行为不变。"""
    adapter = _RecordingAdapter(
        [_read_round(0), _read_round(1), Message(role="assistant", content="改好了")]
    )
    executor = _ScriptedExecutor()
    rt = _runtime(tmp_path, adapter, executor)

    out = rt._run_conversation("看一眼这两个文件", schemas=[])

    assert out == "改好了"
    assert executor.calls == ["read_file", "read_file"]
    assert _rounds_with(adapter, _PROGRESS) == []
    assert _rounds_with(adapter, _FINAL) == []


# --------------------------------------------------------------------------- #
# 9. P2-2 不回归
# --------------------------------------------------------------------------- #


def test_sync_hint_survives_progress_checkpoints(tmp_path):
    """P2-2：首轮 sync system hint 仍在，且不被 checkpoint 覆盖/重写。"""
    report = SyncReport(status=CONFLICT, conflict_kind="content_divergence", detail="双方分叉")
    adapter = _RecordingAdapter([_read_round(i) for i in range(8)])
    rt = _runtime(tmp_path, adapter, _ScriptedExecutor(), report=report)

    rt._run_conversation("修 bug", schemas=[])

    hit = _rounds_with(adapter, _PROGRESS)
    assert hit
    for msgs in adapter.rounds:
        first = msgs[0]
        assert getattr(first, "role", None) == "system"
        content = getattr(first, "content", "") or ""
        assert "sync=CONFLICT" in content
        assert "forge_sync" in content
        assert "禁止" in content
    # checkpoint 是独立的一条 system 消息，不改写同步状态那条
    ck = _checkpoint_text(adapter, hit[0], _PROGRESS)
    assert "sync=" not in ck
    # 同步状态仍只有一份
    for msgs in adapter.rounds:
        n = sum(1 for t in _system_texts(msgs) if "## 同步状态" in t)
        assert n == 1


def test_working_set_injection_still_present(tmp_path):
    """checkpoint 不取代 Working Set 注入，两者并存。"""
    adapter = _RecordingAdapter([_read_round(i) for i in range(8)])
    rt = _runtime(tmp_path, adapter, _ScriptedExecutor())

    rt._run_conversation("修 bug", schemas=[])

    hit = _rounds_with(adapter, _PROGRESS)
    assert hit
    texts = _system_texts(adapter.rounds[hit[0]])
    assert any(t.startswith("[Working Set]") for t in texts)


# --------------------------------------------------------------------------- #
# 10. 不产生新的工具调用 / 不进持久历史
# --------------------------------------------------------------------------- #


def test_checkpoint_adds_no_tool(tmp_path):
    """checkpoint 只是上下文提示：不新增工具、不改工具面、不产生工具调用。"""
    schemas = [{"name": "read_file", "description": "x", "parameters": {}}]
    adapter = _RecordingAdapter([_read_round(i) for i in range(8)])
    executor = _ScriptedExecutor()
    rt = _runtime(tmp_path, adapter, executor)

    rt._run_conversation("长任务", schemas=schemas)

    for seen in adapter.schemas_seen:
        assert [s["name"] for s in seen] == ["read_file"]
    assert set(executor.calls) == {"read_file"}
    assert not any("progress" in c or "checkpoint" in c for c in executor.calls)


def test_checkpoint_not_persisted_in_conversation(tmp_path):
    """checkpoint 只影响下一轮决策，不进持久 conversation、不污染最终回复。"""
    adapter = _RecordingAdapter(
        [_read_round(i) for i in range(8)] + [Message(role="assistant", content="完成")]
    )
    rt = _runtime(tmp_path, adapter, _ScriptedExecutor())

    out = rt._run_conversation("长任务", schemas=[])

    assert out == "完成"
    assert _PROGRESS not in out
    for m in rt.conversation.get_messages():
        content = getattr(m, "content", "") or ""
        assert _PROGRESS not in content
        assert _FINAL not in content

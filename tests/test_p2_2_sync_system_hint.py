"""P2-2 契约测试：启动/冲突提示进入 Agent 首轮 system 上下文。

语义边界（不要扩大）：
- `_startup_sync_check` 发现同步状态后，不再只写 stderr，而是把状态作为
  首轮 system 上下文的一等信息注入，让 Agent 明确下一步该做什么。
- 注入必须真正进入 Agent 首轮可见的 system 消息，不是 stderr / assistant 文本。
- 复用现有 `SyncReport` / `sync_status()`，不重做同步模型。
- 只注入一次，不污染后续轮次。
- 不改变原有 Veritas mutation 路径（纯文本追加，不动工具声明/守卫）。

用例映射：
  A. CONFLICT → 首轮 system 含 CONFLICT + forge_sync + 禁止 mutation
  B. FAST_FORWARD(World→Disk) → 有方向 + forge_sync + 禁止 mutation
  C. FAST_FORWARD(Disk→World) → 有方向 + forge_sync + 禁止 mutation
  D. IN_SYNC → 明确 IN_SYNC 且不阻塞
  E. WORLD_UNAVAILABLE → direct_disk 可用提示
  F. NOT_A_GIT_REPO → 同步不可用提示
  G. 首轮只注入一次（进入真实 system 消息，非 stderr）
  H. 不改变原有 Veritas mutation 路径（纯文本追加）
"""
from __future__ import annotations

from types import SimpleNamespace

from forge.conversation import Conversation
from forge.runtime import Runtime, _sync_status_system_hint
from forge.sync.sync_layer import (
    CONFLICT,
    FAST_FORWARD_DISK_TO_WORLD,
    FAST_FORWARD_WORLD_TO_DISK,
    IN_SYNC,
    NOT_A_GIT_REPO,
    WORLD_UNAVAILABLE,
    SyncReport,
)


def _rt(tmp_path, report):
    """构造最小 Runtime，只设 workspace + sync_layer，供 _initial_system 使用。"""
    rt = Runtime.__new__(Runtime)
    rt.workspace = SimpleNamespace(project_root=str(tmp_path))
    rt.sync_layer = SimpleNamespace(detect=lambda: report)
    return rt


# --------------------------------------------------------------------------- #
# A~F：纯函数格式化（每个状态）
# --------------------------------------------------------------------------- #


def test_conflict_hint_has_conflict_forge_sync_and_block(tmp_path):
    report = SyncReport(status=CONFLICT, conflict_kind="content_divergence", detail="双方分叉")
    h = _sync_status_system_hint(report)
    assert "sync=CONFLICT" in h
    assert "forge_sync" in h
    assert "禁止" in h
    assert "mutation" in h
    assert "分叉" in h


def test_fast_forward_world_to_disk_hint_has_direction(tmp_path):
    report = SyncReport(status=FAST_FORWARD_WORLD_TO_DISK, detail="World 有未物化 receipt")
    h = _sync_status_system_hint(report)
    assert "sync=FAST_FORWARD_WORLD_TO_DISK" in h
    assert "World → Disk" in h
    assert "forge_sync" in h
    assert "禁止" in h


def test_fast_forward_disk_to_world_hint_has_direction(tmp_path):
    report = SyncReport(status=FAST_FORWARD_DISK_TO_WORLD, detail="外部 git commit")
    h = _sync_status_system_hint(report)
    assert "sync=FAST_FORWARD_DISK_TO_WORLD" in h
    assert "Disk → World" in h
    assert "forge_sync" in h
    assert "禁止" in h


def test_in_sync_hint_is_non_blocking(tmp_path):
    report = SyncReport(status=IN_SYNC)
    h = _sync_status_system_hint(report)
    assert "sync=IN_SYNC" in h
    assert "禁止" not in h
    assert "forge_sync" not in h  # 无需对账，不阻塞


def test_world_unavailable_hint_mentions_direct_disk(tmp_path):
    report = SyncReport(status=WORLD_UNAVAILABLE, detail="veritasd offline")
    h = _sync_status_system_hint(report)
    assert "sync=WORLD_UNAVAILABLE" in h
    assert "direct_disk" in h
    assert "不可达" in h


def test_not_a_git_repo_hint_marks_sync_unavailable(tmp_path):
    report = SyncReport(status=NOT_A_GIT_REPO)
    h = _sync_status_system_hint(report)
    assert "sync=NOT_A_GIT_REPO" in h
    assert "同步能力不可用" in h


def test_unknown_status_hint_is_empty(tmp_path):
    assert _sync_status_system_hint(None) == ""
    assert _sync_status_system_hint(SyncReport(status="SOMETHING_ELSE")) == ""


# --------------------------------------------------------------------------- #
# G. 只注入一次，且真正进入首轮 system 消息
# --------------------------------------------------------------------------- #


def test_initial_system_injects_hint_once(tmp_path):
    report = SyncReport(status=CONFLICT, conflict_kind="content_divergence", detail="x")
    rt = _rt(tmp_path, report)
    system = rt._initial_system("")
    assert system.count("## 同步状态") == 1
    assert "sync=CONFLICT" in system
    assert "forge_sync" in system


class _CaptureAdapter:
    def __init__(self):
        self.messages = None

    def send(self, messages, schemas):
        self.messages = list(messages)
        return SimpleNamespace(tool_calls=[], content="ok")


def test_run_conversation_puts_hint_in_first_visible_system_message(tmp_path):
    """注入必须进入 Agent 首轮可见的 system 消息，而非 stderr。"""
    report = SyncReport(status=CONFLICT, conflict_kind="content_divergence", detail="双方分叉")
    rt = Runtime.__new__(Runtime)
    rt.workspace = SimpleNamespace(project_root=str(tmp_path))
    rt.sync_layer = SimpleNamespace(detect=lambda: report)
    rt.conversation = Conversation()
    rt.adapter = _CaptureAdapter()
    rt.executor = SimpleNamespace(tools={})

    result = rt._run_conversation("写一个工具函数", [], "")

    assert result == "ok"
    assert rt.adapter.messages is not None
    first = rt.adapter.messages[0]
    assert getattr(first, "role", None) == "system"
    content = getattr(first, "content", "")
    assert "sync=CONFLICT" in content
    assert "forge_sync" in content
    assert "禁止" in content
    # 同步提示只出现在首轮那一条 system 消息里，不重复注入
    sync_messages = [
        m
        for m in rt.adapter.messages
        if getattr(m, "role", None) == "system"
        and "## 同步状态" in (getattr(m, "content", "") or "")
    ]
    assert len(sync_messages) == 1


# --------------------------------------------------------------------------- #
# H. 不改变原有 Veritas mutation 路径
# --------------------------------------------------------------------------- #


def test_hint_is_pure_text_and_mutation_path_unchanged(tmp_path):
    """注入只改 system 文本：base 指令不变，mutation 工具面/守卫路径不变。"""
    from forge.system_prompt import SYSTEM_INSTRUCTION
    from forge.tools.schemas import MUTATION_TOOL_NAMES

    report = SyncReport(status=IN_SYNC)
    rt = _rt(tmp_path, report)
    system = rt._initial_system("")

    assert system.startswith(SYSTEM_INSTRUCTION)  # base 指令逐字保留，仅追加
    # mutation 工具面（含对账入口 forge_sync）不因注入变化
    from forge.tools.schemas import RECONCILIATION_TOOL_NAMES
    assert {"str_replace", "write_file"} <= set(MUTATION_TOOL_NAMES)
    assert "forge_sync" in RECONCILIATION_TOOL_NAMES
    assert "forge_sync" not in MUTATION_TOOL_NAMES
    # 注入不改写 sync_layer 对象本身
    assert rt.sync_layer is not None

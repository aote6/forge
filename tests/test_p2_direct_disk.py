"""P2-1 契约测试：无 Veritas 时的一等直写路径（direct_disk）。

语义边界（不要扩大）：
- veritasd 不可用 → 文件内容 mutation（str_replace / write_file）自动走
  direct_disk 本地写盘，而不是硬失败。
- Veritas 可用 → 完全走原有 World 事务路径，行为不变。
- World object 操作（create_object / link_objects）没有磁盘等价物，
  veritasd 不可用时必须继续硬失败，不得伪装成 direct_disk。
- direct_disk 不绕过 P1 的 VERIFY_REQUIRED / 外部变更 guard。

用例映射：
  A. veritasd 不可用 → 自动 direct_disk
  B. direct_disk str_replace 成功且磁盘内容正确
  C. direct_disk write_file 成功且磁盘内容正确
  D. direct_disk 产生正确 session_changes / undo 信息
  E. Veritas 可用时仍走原事务路径
  F. display/result 明确 mode=direct_disk
  G. VERIFY_REQUIRED guard 在 direct_disk 下仍有效
  H. direct_disk 失败时不产生错误的成功状态
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from forge.adapters.base import ToolResult
from forge.projections.base import ProjectionResult
from forge.tools import session_changes as sc
from forge.tools.direct_disk import (
    DIRECT_DISK_TOOLS,
    MODE_DIRECT_DISK,
    world_available,
)
from forge.tools.intent_tools import make_intent_tools


# --------------------------------------------------------------------------- #
# fixtures：复用现有 MagicMock executor + SimpleNamespace world 模式
# --------------------------------------------------------------------------- #


def _offline_world(tmp_path):
    """veritasd 不可用：get_version() 抛异常（与 SyncLayer.world_available 同源探测）。"""

    def _boom():
        raise RuntimeError("veritasd offline")

    return SimpleNamespace(
        project_root=str(tmp_path), _path_map=None, get_version=_boom
    )


def _online_world(tmp_path, oid=1001):
    """veritasd 可用：get_version() 正常返回，且 path→oid 可解析。"""
    return SimpleNamespace(
        project_root=str(tmp_path),
        _path_map=None,
        get_version=lambda: 7,
        find_object_id=lambda p: oid,
    )


def _offline_tools(tmp_path):
    executor = MagicMock()
    executor._world = _offline_world(tmp_path)
    projections = MagicMock()
    return make_intent_tools(executor, projections), executor, projections


def _online_tools(tmp_path, oid=1001):
    executor = MagicMock()
    executor._world = _online_world(tmp_path, oid=oid)
    receipt = SimpleNamespace(tx_id=77, version=12, before_root="b", after_root="a")
    delta = SimpleNamespace(objects_created=[], metadata={})
    executor.execute.return_value = (receipt, delta)
    projections = MagicMock()
    projections.project.return_value = [ProjectionResult(name="file", success=True)]
    return make_intent_tools(executor, projections), executor, projections, receipt


@pytest.fixture(autouse=True)
def _clean_session_log():
    sc.clear()
    yield
    sc.clear()


# --------------------------------------------------------------------------- #
# A. veritasd 不可用 → 自动 direct_disk
# --------------------------------------------------------------------------- #


def test_world_available_false_when_probe_raises(tmp_path):
    assert world_available(_offline_world(tmp_path)) is False


def test_world_available_true_when_probe_ok(tmp_path):
    assert world_available(_online_world(tmp_path)) is True


def test_world_available_false_when_world_is_none():
    assert world_available(None) is False


def test_world_available_defaults_true_when_unprobeable():
    """无法探测（老 fixture / 无 get_version）→ 假定可用，保持既有 Veritas 路径。"""
    assert world_available(SimpleNamespace(project_root="/tmp")) is True


def test_offline_str_replace_selects_direct_disk_without_veritas_tx(tmp_path):
    """A：veritasd 不可用时自动进入 direct_disk，且不尝试任何 World 事务。"""
    f = tmp_path / "pkg"
    f.mkdir()
    (f / "a.py").write_text("x = 1\n", encoding="utf-8")

    tools, executor, projections = _offline_tools(tmp_path)
    r = tools["str_replace"](path="pkg/a.py", old_string="x = 1", new_string="x = 2")

    assert r.success is True
    assert r.payload.get("mode") == MODE_DIRECT_DISK
    assert r.payload.get("direct_disk") is True
    assert r.payload.get("world_recorded") is False
    executor.execute.assert_not_called()
    executor.execute_batch.assert_not_called()
    projections.project.assert_not_called()


# --------------------------------------------------------------------------- #
# B/C. 磁盘内容正确
# --------------------------------------------------------------------------- #


def test_direct_disk_str_replace_writes_disk(tmp_path):
    (tmp_path / "pkg").mkdir()
    target = tmp_path / "pkg" / "a.py"
    target.write_text("def f():\n    return 1\n", encoding="utf-8")

    tools, _, _ = _offline_tools(tmp_path)
    r = tools["str_replace"](path="pkg/a.py", old_string="return 1", new_string="return 2")

    assert r.success is True
    assert target.read_text(encoding="utf-8") == "def f():\n    return 2\n"


def test_direct_disk_str_replace_keeps_near_miss_semantics(tmp_path):
    """direct_disk 不放宽匹配语义：old_string 不存在仍然失败。"""
    (tmp_path / "a.py").write_text("alpha\n", encoding="utf-8")
    tools, _, _ = _offline_tools(tmp_path)
    r = tools["str_replace"](path="a.py", old_string="beta", new_string="gamma")
    assert r.success is False
    assert (tmp_path / "a.py").read_text(encoding="utf-8") == "alpha\n"


def test_direct_disk_write_file_writes_disk(tmp_path):
    tools, executor, _ = _offline_tools(tmp_path)
    r = tools["write_file"](path="new/deep/b.py", content="print('hi')\n")

    assert r.success is True
    assert r.payload.get("mode") == MODE_DIRECT_DISK
    assert (tmp_path / "new" / "deep" / "b.py").read_text(encoding="utf-8") == "print('hi')\n"
    executor.execute.assert_not_called()


def test_direct_disk_write_file_overwrites_existing(tmp_path):
    target = tmp_path / "c.py"
    target.write_text("old\n", encoding="utf-8")
    tools, _, _ = _offline_tools(tmp_path)
    r = tools["write_file"](path="c.py", content="new\n")
    assert r.success is True
    assert target.read_text(encoding="utf-8") == "new\n"


# --------------------------------------------------------------------------- #
# D. session_changes / shadow undo 语义保持
# --------------------------------------------------------------------------- #


def test_direct_disk_records_session_change(tmp_path):
    (tmp_path / "a.py").write_text("v = 1\n", encoding="utf-8")
    tools, _, _ = _offline_tools(tmp_path)
    tools["str_replace"](path="a.py", old_string="v = 1", new_string="v = 2")

    changes = sc.list_changes()
    assert len(changes) == 1
    entry = changes[0]
    assert entry["path"] == "a.py"
    assert entry["tool"] == "str_replace"
    assert entry["tx"]  # 直写也必须有可追溯的 tx 标识
    assert MODE_DIRECT_DISK in entry["summary"]

    persisted = tmp_path / ".forge" / "session_changes.jsonl"
    assert persisted.is_file()
    assert json.loads(persisted.read_text(encoding="utf-8").splitlines()[0])["path"] == "a.py"


def test_direct_disk_undo_restores_previous_content(tmp_path):
    target = tmp_path / "a.py"
    target.write_text("v = 1\n", encoding="utf-8")
    tools, _, _ = _offline_tools(tmp_path)

    tools["str_replace"](path="a.py", old_string="v = 1", new_string="v = 2")
    assert target.read_text(encoding="utf-8") == "v = 2\n"

    undo = tools["undo_last_tx"]()
    assert undo.success is True
    assert target.read_text(encoding="utf-8") == "v = 1\n"


def test_direct_disk_write_file_undo_restores_previous_content(tmp_path):
    target = tmp_path / "c.py"
    target.write_text("old\n", encoding="utf-8")
    tools, _, _ = _offline_tools(tmp_path)

    tools["write_file"](path="c.py", content="new\n")
    undo = tools["undo_last_tx"]()
    assert undo.success is True
    assert target.read_text(encoding="utf-8") == "old\n"


def test_direct_disk_tx_ids_are_unique(tmp_path):
    (tmp_path / "a.py").write_text("1\n", encoding="utf-8")
    tools, _, _ = _offline_tools(tmp_path)
    r1 = tools["write_file"](path="a.py", content="2\n")
    r2 = tools["write_file"](path="a.py", content="3\n")
    assert r1.payload["tx_id"] != r2.payload["tx_id"]

    # shadow 栈两层都在，undo 两次回到最初内容
    tools["undo_last_tx"]()
    tools["undo_last_tx"]()
    assert (tmp_path / "a.py").read_text(encoding="utf-8") == "1\n"


# --------------------------------------------------------------------------- #
# E. Veritas 可用 → 原事务路径不变
# --------------------------------------------------------------------------- #


def test_online_str_replace_uses_veritas_transaction(tmp_path):
    (tmp_path / "a.py").write_text("v = 1\n", encoding="utf-8")
    tools, executor, projections, receipt = _online_tools(tmp_path)

    r = tools["str_replace"](path="a.py", old_string="v = 1", new_string="v = 2")

    assert r.success is True
    executor.execute.assert_called_once()
    projections.project.assert_called_once()
    assert r.payload.get("tx_id") == receipt.tx_id
    assert r.payload.get("version") == receipt.version
    assert r.payload.get("mode") != MODE_DIRECT_DISK
    assert r.payload.get("direct_disk") is not True
    assert MODE_DIRECT_DISK not in (r.display or "")


def test_online_write_file_uses_veritas_transaction(tmp_path):
    (tmp_path / "a.py").write_text("v = 1\n", encoding="utf-8")
    tools, executor, projections, receipt = _online_tools(tmp_path)

    r = tools["write_file"](path="a.py", content="v = 9\n")

    assert r.success is True
    executor.execute.assert_called_once()
    assert r.payload.get("tx_id") == receipt.tx_id
    assert r.payload.get("mode") == "overwrite"
    assert MODE_DIRECT_DISK not in (r.display or "")


# --------------------------------------------------------------------------- #
# F. display / result 明确标识 mode=direct_disk
# --------------------------------------------------------------------------- #


def test_direct_disk_display_marks_mode(tmp_path):
    (tmp_path / "a.py").write_text("v = 1\n", encoding="utf-8")
    tools, _, _ = _offline_tools(tmp_path)
    r = tools["str_replace"](path="a.py", old_string="v = 1", new_string="v = 2")
    assert f"mode={MODE_DIRECT_DISK}" in (r.display or "")


def test_direct_disk_display_explains_world_not_recorded(tmp_path):
    tools, _, _ = _offline_tools(tmp_path)
    r = tools["write_file"](path="a.py", content="x\n")
    display = r.display or ""
    assert f"mode={MODE_DIRECT_DISK}" in display
    assert "forge_sync" in display  # 恢复 veritasd 后需要对账


# --------------------------------------------------------------------------- #
# G. P1 guard 在 direct_disk 下仍有效 + World object 操作不得伪装
# --------------------------------------------------------------------------- #


def _rt_offline():
    from forge.runtime import Runtime, WorkingSet

    rt = Runtime.__new__(Runtime)
    rt._working_set = WorkingSet(goal="g")
    rt.sync_layer = SimpleNamespace(
        world_available=lambda: False,
        disk_change_detected=lambda: False,
        external_change_detected=lambda: True,
    )
    return rt


def test_verify_guard_still_blocks_when_world_offline():
    """G：VERIFY_REQUIRED 待验证时，direct_disk 也不得开始无关编辑。"""
    from forge.runtime import WorkingSet

    rt = _rt_offline()
    ws = WorkingSet(goal="g")
    ws.verify_targets = ["tests/test_a.py"]
    ws.pending_verify = ["verify edit on pkg/a.py"]
    ws.verify_map = {"pkg/a.py": {"tests/test_a.py"}}
    rt._working_set = ws

    blocked = rt._guard_pending_verify("str_replace", {"path": "pkg/b.py"})
    assert blocked is not None and blocked.success is False
    # 修复 pending 文件仍放行
    assert rt._guard_pending_verify("str_replace", {"path": "pkg/a.py"}) is None


def test_external_guard_allows_direct_disk_tools_when_world_offline():
    rt = _rt_offline()
    for name in sorted(DIRECT_DISK_TOOLS):
        assert rt._guard_external_change(name) is None, name


def test_external_guard_still_blocks_world_object_ops_when_offline():
    """World object 操作没有磁盘等价物 → 必须继续硬失败。"""
    rt = _rt_offline()
    for name in ("create_object", "link_objects", "unlink_objects"):
        r = rt._guard_external_change(name)
        assert r is not None and r.success is False, name
        assert "veritasd" in (r.display or "")


def test_external_guard_still_blocks_on_disk_change_when_offline():
    """P1 外部磁盘变更 guard 不被 direct_disk 绕过。"""
    rt = _rt_offline()
    rt.sync_layer = SimpleNamespace(
        world_available=lambda: False,
        disk_change_detected=lambda: True,
        external_change_detected=lambda: True,
    )
    r = rt._guard_external_change("str_replace")
    assert r is not None and r.success is False
    assert "forge_sync" in (r.display or "")


def test_external_guard_unchanged_when_world_online():
    rt = _rt_offline()
    rt.sync_layer = SimpleNamespace(
        world_available=lambda: True,
        disk_change_detected=lambda: True,
        external_change_detected=lambda: True,
    )
    r = rt._guard_external_change("str_replace")
    assert r is not None and r.success is False
    assert "外部磁盘/Git 变化" in (r.display or "")


def test_direct_disk_tool_set_covers_file_mutations_only():
    """P2-1b：DIRECT_DISK_TOOLS 扩到全部文件内容 mutation + undo，仍不含 World object 操作。"""
    assert DIRECT_DISK_TOOLS == frozenset(
        {
            "str_replace",
            "write_file",
            "undo_last_tx",
            "create_file",
            "modify_file",
            "apply_patch",
            "edit_files_batch",
            "delete_file",
        }
    )
    # World object 操作仍不得伪装成 direct_disk。
    for name in ("create_object", "link_objects", "unlink_objects"):
        assert name not in DIRECT_DISK_TOOLS


def test_offline_create_object_does_not_fake_direct_disk(tmp_path):
    """工具层同样不得把 World object 操作降级成 direct_disk。"""
    executor = MagicMock()
    executor._world = _offline_world(tmp_path)
    executor.execute.side_effect = RuntimeError("veritasd offline")
    tools = make_intent_tools(executor, MagicMock())
    r = tools["create_object"]()
    assert r.success is False
    assert MODE_DIRECT_DISK not in (r.display or "")


# --------------------------------------------------------------------------- #
# H. direct_disk 失败 → 不得产生错误的成功状态
# --------------------------------------------------------------------------- #


def test_direct_disk_write_failure_returns_fail(tmp_path):
    (tmp_path / "adir").mkdir()
    tools, _, _ = _offline_tools(tmp_path)
    r = tools["write_file"](path="adir", content="boom\n")

    assert r.success is False
    assert MODE_DIRECT_DISK in (r.display or "")
    assert r.payload.get("direct_disk") is True
    assert r.payload.get("world_recorded") is False


def test_direct_disk_write_failure_records_no_session_change(tmp_path):
    (tmp_path / "adir").mkdir()
    tools, _, _ = _offline_tools(tmp_path)
    tools["write_file"](path="adir", content="boom\n")

    assert sc.list_changes() == []
    assert not (tmp_path / ".forge" / "tx_shadow" / "stack.json").exists()


def test_direct_disk_failure_then_undo_has_nothing_to_undo(tmp_path):
    (tmp_path / "adir").mkdir()
    tools, _, _ = _offline_tools(tmp_path)
    tools["write_file"](path="adir", content="boom\n")
    undo = tools["undo_last_tx"]()
    assert undo.success is False


# --------------------------------------------------------------------------- #
# I. P2-1b：其余文件 mutation 的 direct_disk 直写
# --------------------------------------------------------------------------- #


def test_direct_disk_create_file_writes_disk(tmp_path):
    tools, executor, _ = _offline_tools(tmp_path)
    r = tools["create_file"](path="new/deep/c.py", content="x = 1\n")

    assert r.success is True
    assert r.payload.get("mode") == MODE_DIRECT_DISK
    assert r.payload.get("direct_disk") is True
    assert r.payload.get("world_recorded") is False
    assert (tmp_path / "new" / "deep" / "c.py").read_text(encoding="utf-8") == "x = 1\n"
    executor.execute.assert_not_called()


def test_direct_disk_modify_file_applies_ops(tmp_path):
    target = tmp_path / "m.py"
    target.write_text("a = 1\nb = 2\n", encoding="utf-8")
    tools, executor, _ = _offline_tools(tmp_path)

    r = tools["modify_file"](
        path="m.py",
        operations=[{"type": "replace", "start_line": 1, "end_line": 1, "new_text": "a = 9\n"}],
    )

    assert r.success is True
    assert r.payload.get("mode") == MODE_DIRECT_DISK
    assert target.read_text(encoding="utf-8") == "a = 9\nb = 2\n"
    executor.execute.assert_not_called()


def test_direct_disk_modify_file_missing_file_fails(tmp_path):
    tools, executor, _ = _offline_tools(tmp_path)
    r = tools["modify_file"](
        path="nope.py",
        operations=[{"type": "replace", "start_line": 1, "end_line": 1, "new_text": "x\n"}],
    )
    assert r.success is False
    executor.execute.assert_not_called()


def test_direct_disk_apply_patch_writes_disk(tmp_path):
    target = tmp_path / "m.py"
    target.write_text("a = 1\nb = 2\n", encoding="utf-8")
    tools, executor, _ = _offline_tools(tmp_path)

    patch = "--- a/m.py\n+++ b/m.py\n@@ -1 +1 @@\n-a = 1\n+a = 9\n"
    r = tools["apply_patch"](patch=patch)

    assert r.success is True
    assert r.payload.get("mode") == MODE_DIRECT_DISK
    assert target.read_text(encoding="utf-8") == "a = 9\nb = 2\n"
    executor.execute.assert_not_called()
    executor.execute_batch.assert_not_called()


def test_direct_disk_edit_files_batch_writes_disk(tmp_path):
    a = tmp_path / "a.py"
    b = tmp_path / "b.py"
    a.write_text("a = 1\n", encoding="utf-8")
    b.write_text("b = 1\n", encoding="utf-8")
    tools, executor, _ = _offline_tools(tmp_path)

    r = tools["edit_files_batch"](
        edits=[
            {"path": "a.py", "operations": [{"type": "replace", "start_line": 1, "end_line": 1, "new_text": "a = 2\n"}]},
            {"path": "b.py", "operations": [{"type": "replace", "start_line": 1, "end_line": 1, "new_text": "b = 2\n"}]},
        ]
    )

    assert r.success is True
    assert r.payload.get("mode") == MODE_DIRECT_DISK
    assert a.read_text(encoding="utf-8") == "a = 2\n"
    assert b.read_text(encoding="utf-8") == "b = 2\n"
    executor.execute.assert_not_called()
    executor.execute_batch.assert_not_called()

    # 批量编辑共享一个 shadow 条目，undo 一次恢复两个文件。
    tools["undo_last_tx"]()
    assert a.read_text(encoding="utf-8") == "a = 1\n"
    assert b.read_text(encoding="utf-8") == "b = 1\n"


def test_direct_disk_delete_file_removes_disk_and_undo_restores(tmp_path):
    target = tmp_path / "d.py"
    target.write_text("d = 1\n", encoding="utf-8")
    tools, executor, _ = _offline_tools(tmp_path)

    r = tools["delete_file"](path="d.py")
    assert r.success is True
    assert r.payload.get("mode") == MODE_DIRECT_DISK
    assert not target.exists()
    executor.execute.assert_not_called()

    tools["undo_last_tx"]()
    assert target.read_text(encoding="utf-8") == "d = 1\n"


def test_direct_disk_delete_file_requires_path(tmp_path):
    tools, executor, _ = _offline_tools(tmp_path)
    r = tools["delete_file"](object_id=123)
    assert r.success is False
    assert "path" in (r.display or "")
    executor.execute.assert_not_called()


# --------------------------------------------------------------------------- #
# J. P2-1c：direct_disk 待对账标记 + 复线对账提示
# --------------------------------------------------------------------------- #


def test_direct_disk_mutation_marks_session_change(tmp_path):
    tools, _, _ = _offline_tools(tmp_path)
    tools["create_file"](path="k.py", content="k = 1\n")

    changes = sc.list_changes()
    assert len(changes) == 1
    assert changes[0]["direct_disk"] is True
    assert MODE_DIRECT_DISK in changes[0]["summary"]


def test_pending_direct_disk_reads_persisted_entries(tmp_path):
    # 跨进程视角：直接读持久化文件，不依赖进程内 _LOG。
    sc.record(
        "a.py", tool="write_file", tx_id="direct-1",
        summary="write_file mode=direct_disk",
        project_root=str(tmp_path), direct_disk=True,
    )
    sc.record(
        "b.py", tool="str_replace", tx_id="tx-2",
        summary="str_replace mode=overwrite",
        project_root=str(tmp_path),
    )

    pending = sc.pending_direct_disk(str(tmp_path))
    assert len(pending) == 1
    assert pending[0]["path"] == "a.py"


def test_reconcile_hint_when_world_available(tmp_path):
    from forge.runtime import _direct_disk_reconcile_hint

    sc.record("a.py", tool="write_file", tx_id="direct-1",
             summary="mode=direct_disk", project_root=str(tmp_path), direct_disk=True)

    hint = _direct_disk_reconcile_hint(str(tmp_path), True)
    assert "direct_disk 待对账" in hint
    assert "a.py" in hint
    assert "forge_sync" in hint


def test_reconcile_hint_empty_when_world_unavailable(tmp_path):
    from forge.runtime import _direct_disk_reconcile_hint

    sc.record("a.py", tool="write_file", tx_id="direct-1",
             summary="mode=direct_disk", project_root=str(tmp_path), direct_disk=True)
    assert _direct_disk_reconcile_hint(str(tmp_path), False) == ""


def test_reconcile_hint_empty_when_no_pending(tmp_path):
    from forge.runtime import _direct_disk_reconcile_hint

    assert _direct_disk_reconcile_hint(str(tmp_path), True) == ""


def test_forge_sync_appends_reconcile_hint(tmp_path):
    from forge.workspace import Workspace
    from forge.tools import make_tools

    sc.record("a.py", tool="write_file", tx_id="direct-1",
             summary="mode=direct_disk", project_root=str(tmp_path), direct_disk=True)

    workspace = Workspace(project_root=str(tmp_path))
    world = MagicMock()
    projections = MagicMock()
    sync_layer = MagicMock()
    sync_layer.project_root = str(tmp_path)
    report = MagicMock()
    report.status = "IN_SYNC"
    report.format.return_value = "sync_status: IN_SYNC"
    report.to_dict.return_value = {"status": "IN_SYNC"}
    sync_layer.sync.return_value = report

    tools, _, _ = make_tools(
        workspace=workspace,
        world_runtime=world,
        projections=projections,
        allow_mutation=True,
        sync_layer=sync_layer,
    )
    r = tools["forge_sync"]()
    assert r.success is True
    assert "direct_disk 待对账" in (r.display or "")
    assert "a.py" in (r.display or "")

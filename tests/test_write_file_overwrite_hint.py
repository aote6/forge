"""write_file 覆盖提示契约测试。

原 bug：write_file 把「覆盖了已存在文件(N行)…建议用 str_replace」提示写进
result.display，紧接着 _attach_diff 用 format_block 整体重建 display，提示被丢弃，
从未真正到达模型。World 路径与 direct_disk 路径都一样。

修复后该提示并入 _attach_diff 的 hint，display 重建后仍在。
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from forge.projections.base import ProjectionResult
from forge.tools.direct_disk import MODE_DIRECT_DISK
from forge.tools.intent_tools import make_intent_tools


# --------------------------------------------------------------------------- #
# fixtures（复用 test_p2_direct_disk.py 的 online/offline world 模式）
# --------------------------------------------------------------------------- #


def _offline_world(tmp_path):
    def _boom():
        raise RuntimeError("veritasd offline")

    return SimpleNamespace(
        project_root=str(tmp_path), _path_map=None, get_version=_boom
    )


def _online_world(tmp_path, oid=1001):
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
    return make_intent_tools(executor, projections)


def _online_tools(tmp_path, oid=1001):
    executor = MagicMock()
    executor._world = _online_world(tmp_path, oid=oid)
    receipt = SimpleNamespace(tx_id=77, version=12, before_root="b", after_root="a")
    delta = SimpleNamespace(objects_created=[], metadata={})
    executor.execute.return_value = (receipt, delta)
    projections = MagicMock()
    projections.project.return_value = [ProjectionResult(name="file", success=True)]
    return make_intent_tools(executor, projections)


# --------------------------------------------------------------------------- #
# 契约：覆盖已存在文件 → display 必须保留覆盖提示
# --------------------------------------------------------------------------- #


def test_world_overwrite_display_keeps_hint(tmp_path):
    """World 路径覆盖已存在文件：display 必须含覆盖提示。"""
    target = tmp_path / "a.py"
    target.write_text("x = 1\ny = 2\nz = 3\n", encoding="utf-8")
    tools = _online_tools(tmp_path)

    r = tools["write_file"](path="a.py", content="x = 99\n")

    assert r.success is True
    assert "覆盖了已存在文件" in (r.display or "")
    assert "str_replace" in (r.display or "")


def test_direct_disk_overwrite_display_keeps_hint(tmp_path):
    """direct_disk 路径覆盖已存在文件：display 也必须含覆盖提示。"""
    target = tmp_path / "a.py"
    target.write_text("x = 1\ny = 2\nz = 3\n", encoding="utf-8")
    tools = _offline_tools(tmp_path)

    r = tools["write_file"](path="a.py", content="x = 99\n")

    assert r.success is True
    assert r.payload.get("mode") == MODE_DIRECT_DISK
    assert "覆盖了已存在文件" in (r.display or "")
    assert "str_replace" in (r.display or "")


def test_new_file_has_no_overwrite_hint(tmp_path):
    """创建新文件（无既有内容）不应出现覆盖提示。"""
    tools = _offline_tools(tmp_path)
    r = tools["write_file"](path="brand_new.py", content="print('hi')\n")
    assert r.success is True
    assert "覆盖了已存在文件" not in (r.display or "")


def test_same_content_has_no_overwrite_hint(tmp_path):
    """覆盖内容与既有内容一致 → 不算覆盖，不应出现提示。"""
    target = tmp_path / "a.py"
    target.write_text("x = 1\n", encoding="utf-8")
    tools = _online_tools(tmp_path)

    r = tools["write_file"](path="a.py", content="x = 1\n")

    assert r.success is True
    assert "覆盖了已存在文件" not in (r.display or "")

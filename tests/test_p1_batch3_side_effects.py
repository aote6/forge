"""P0-4 batch3: post-success side effects must be observable, not flip success.

端到端契约：走 tools["write_file"] / tools["str_replace"] 入口注入副作用失败
（而非手动构造 ToolResult + 手动调 helper），断言：
- success 保持 True（主操作成功不被附属副作用翻转）
- payload.side_effect_warnings 记录失败标签
- display 含 SIDE_EFFECT_WARN
- 磁盘主操作本身确实完成（副作用失败不掩盖主结果）
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from forge.projections.base import ProjectionResult
from forge.runtime import _save_session_summary
from forge.tools import session_changes as sc
from forge.tools.intent_tools import make_intent_tools


@pytest.fixture(autouse=True)
def _clean_session_log():
    sc.clear()
    yield
    sc.clear()


def _offline_world(tmp_path):
    """veritasd 不可用 → write_file/str_replace 自动走 direct_disk 本地写盘。"""

    def _boom():
        raise RuntimeError("veritasd offline")

    return SimpleNamespace(project_root=str(tmp_path), _path_map=None, get_version=_boom)


def _offline_tools(tmp_path):
    executor = MagicMock()
    executor._world = _offline_world(tmp_path)
    return make_intent_tools(executor, MagicMock())


# --------------------------------------------------------------------------- #
# write_file：record_tx/memory/cache 失败 → success 保持 + 可观测
# --------------------------------------------------------------------------- #


def test_write_file_side_effect_failure_keeps_success(tmp_path):
    tools = _offline_tools(tmp_path)
    with patch(
        "forge.tools.intent_tools.record_tx", side_effect=OSError("disk full")
    ):
        r = tools["write_file"](path="a.py", content="hello\n")

    assert r.success is True
    warns = (r.payload or {}).get("side_effect_warnings") or []
    assert any("record_tx/memory/cache" in w for w in warns)
    assert any("disk full" in w for w in warns)
    assert "SIDE_EFFECT_WARN:" in (r.display or "")
    # 主操作本身确实成功写盘，副作用失败不掩盖主结果
    assert (tmp_path / "a.py").read_text(encoding="utf-8") == "hello\n"


# --------------------------------------------------------------------------- #
# str_replace：record_tx/memory/cache 失败 → success 保持 + 可观测
# --------------------------------------------------------------------------- #


def test_str_replace_side_effect_failure_keeps_success(tmp_path):
    (tmp_path / "a.py").write_text("v = 1\n", encoding="utf-8")
    tools = _offline_tools(tmp_path)
    with patch(
        "forge.tools.intent_tools.record_tx", side_effect=OSError("tx fail")
    ):
        r = tools["str_replace"](
            path="a.py", old_string="v = 1", new_string="v = 2"
        )

    assert r.success is True
    warns = (r.payload or {}).get("side_effect_warnings") or []
    assert any("record_tx/memory/cache" in w for w in warns)
    assert any("tx fail" in w for w in warns)
    assert "SIDE_EFFECT_WARN:" in (r.display or "")
    assert (tmp_path / "a.py").read_text(encoding="utf-8") == "v = 2\n"


# --------------------------------------------------------------------------- #
# write_file：record_session_change 失败（第二个 _note 调用点）→ success 保持
# --------------------------------------------------------------------------- #


def test_record_session_change_failure_keeps_success(tmp_path):
    tools = _offline_tools(tmp_path)
    with patch(
        "forge.tools.intent_tools.record_session_change",
        side_effect=OSError("log fail"),
    ):
        r = tools["write_file"](path="a.py", content="x\n")

    assert r.success is True
    warns = (r.payload or {}).get("side_effect_warnings") or []
    assert any("record_session_change" in w for w in warns)
    assert "SIDE_EFFECT_WARN:" in (r.display or "")
    assert (tmp_path / "a.py").read_text(encoding="utf-8") == "x\n"


# --------------------------------------------------------------------------- #
# path_map 同步失败（World 路径，两条更新路径都抛）→ 不抛异常 + 可观测
# --------------------------------------------------------------------------- #


def test_sync_path_map_failure_does_not_raise(tmp_path):
    (tmp_path / "a.py").write_text("old\n", encoding="utf-8")

    def _boom_update(_delta):
        raise RuntimeError("map broken")

    world = SimpleNamespace(
        project_root=str(tmp_path),
        _path_map=SimpleNamespace(update_from_delta=_boom_update),
        _update_path_map=_boom_update,
        get_version=lambda: 7,
        find_object_id=lambda _p: 1001,
    )
    executor = MagicMock()
    executor._world = world
    receipt = SimpleNamespace(tx_id=9, version=3)
    delta = SimpleNamespace(objects_created=[], metadata={})
    executor.execute.return_value = (receipt, delta)
    projections = MagicMock()
    projections.project.return_value = [ProjectionResult(name="file", success=True)]

    tools = make_intent_tools(executor, projections)
    r = tools["write_file"](path="a.py", content="new\n")

    assert r.success is True
    warns = (r.payload or {}).get("side_effect_warnings") or []
    assert any("_update_path_map" in w for w in warns)
    assert any("update_from_delta" in w for w in warns)
    assert "SIDE_EFFECT_WARN:" in (r.display or "")


def test_save_session_summary_failure_is_logged(tmp_path, capsys):
    root = tmp_path / "proj"
    root.mkdir()
    with patch("pathlib.Path.write_text", side_effect=OSError("quota")):
        _save_session_summary(str(root), ["note1"])
    captured = capsys.readouterr()
    assert "_save_session_summary failed" in captured.err

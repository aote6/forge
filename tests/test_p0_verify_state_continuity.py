"""P0: verify_map 跨 Runtime 恢复后 guard / 精确清账语义连续.

Canonical: verify_map 为 path→targets 权威行为事实；
pending_verify / verify_targets 由 map 同步维护的表达层。
"""
from __future__ import annotations

from forge.adapters.base import ToolResult
from forge.runtime import Runtime, WorkingSet, _save_task_state, _load_task_state


def _rt(ws: WorkingSet) -> Runtime:
    rt = Runtime.__new__(Runtime)
    rt._working_set = ws
    return rt


def _blocked(rt: Runtime, name: str, args: dict) -> bool:
    r = rt._guard_pending_verify(name, args)
    return r is not None and not r.success


def _ws_ab() -> WorkingSet:
    """A.py→test_A, B.py→test_B 的待验证状态。"""
    ws = WorkingSet(goal="resume verify")
    ws.verify_map = {
        "pkg/a.py": {"tests/test_a.py"},
        "pkg/b.py": {"tests/test_b.py"},
    }
    ws._sync_verify_views_from_map()
    return ws


# --------------------------------------------------------------------------- #
# Test 1: 跨 Runtime 恢复 verify_map
# --------------------------------------------------------------------------- #


def test_verify_map_survives_task_state_roundtrip(tmp_path):
    ws = _ws_ab()
    _save_task_state(str(tmp_path), ws)
    data = _load_task_state(str(tmp_path))
    assert data is not None
    assert "verify_map" in data
    assert data["verify_map"]["pkg/a.py"] == ["tests/test_a.py"]
    assert data["verify_map"]["pkg/b.py"] == ["tests/test_b.py"]

    ws2 = WorkingSet.from_dict(data)
    assert ws2.verify_map.get("pkg/a.py") == {"tests/test_a.py"}
    assert ws2.verify_map.get("pkg/b.py") == {"tests/test_b.py"}
    assert "pkg/a.py" in {ws2._pending_entry_path(e) for e in ws2.pending_verify}
    assert "pkg/b.py" in {ws2._pending_entry_path(e) for e in ws2.pending_verify}
    assert "tests/test_a.py" in ws2.verify_targets
    assert "tests/test_b.py" in ws2.verify_targets


# --------------------------------------------------------------------------- #
# Test 2: 恢复后 guard 与未重启一致
# --------------------------------------------------------------------------- #


def test_guard_same_after_restore(tmp_path):
    ws = _ws_ab()
    rt0 = _rt(ws)
    assert not _blocked(rt0, "str_replace", {"path": "pkg/a.py", "old_string": "x", "new_string": "y"})
    assert not _blocked(rt0, "write_file", {"path": "pkg/b.py", "content": "z"})
    assert _blocked(rt0, "str_replace", {"path": "pkg/z.py", "old_string": "x", "new_string": "y"})

    _save_task_state(str(tmp_path), ws)
    ws2 = WorkingSet.from_dict(_load_task_state(str(tmp_path)))
    # 关键：恢复后 verify_map 不得因序列化变空
    assert ws2.verify_map
    assert ws2.verify_targets
    rt1 = _rt(ws2)
    assert not _blocked(rt1, "str_replace", {"path": "pkg/a.py", "old_string": "x", "new_string": "y"})
    assert not _blocked(rt1, "write_file", {"path": "pkg/b.py", "content": "z"})
    assert _blocked(rt1, "str_replace", {"path": "pkg/z.py", "old_string": "x", "new_string": "y"})


# --------------------------------------------------------------------------- #
# Test 3: 恢复后精确清账
# --------------------------------------------------------------------------- #


def test_precise_clear_after_restore(tmp_path):
    ws = _ws_ab()
    _save_task_state(str(tmp_path), ws)
    ws2 = WorkingSet.from_dict(_load_task_state(str(tmp_path)))

    ws2.update_from_tool(
        "run_test_structured",
        {"target": "tests/test_a.py"},
        ToolResult.ok(display="OK", payload={}),
    )
    assert "pkg/a.py" not in ws2.verify_map
    assert "pkg/b.py" in ws2.verify_map
    assert ws2.verify_map["pkg/b.py"] == {"tests/test_b.py"}
    assert "tests/test_a.py" not in ws2.verify_targets
    assert "tests/test_b.py" in ws2.verify_targets


# --------------------------------------------------------------------------- #
# Test 4: 清账后再次恢复
# --------------------------------------------------------------------------- #


def test_clear_then_save_restore_then_finish(tmp_path):
    ws = _ws_ab()
    ws.update_from_tool(
        "run_test_structured",
        {"target": "tests/test_a.py"},
        ToolResult.ok(display="OK", payload={}),
    )
    assert "pkg/a.py" not in ws.verify_map
    assert "pkg/b.py" in ws.verify_map

    _save_task_state(str(tmp_path), ws)
    ws2 = WorkingSet.from_dict(_load_task_state(str(tmp_path)))
    assert "pkg/a.py" not in ws2.verify_map
    assert ws2.verify_map.get("pkg/b.py") == {"tests/test_b.py"}
    assert "tests/test_b.py" in ws2.verify_targets

    ws2.update_from_tool(
        "run_test_structured",
        {"target": "tests/test_b.py"},
        ToolResult.ok(display="OK", payload={}),
    )
    assert ws2.verify_map == {}
    assert ws2.verify_targets == []
    assert ws2.pending_verify == []


# --------------------------------------------------------------------------- #
# Test 5: 坏输入兼容
# --------------------------------------------------------------------------- #


def test_bad_verify_map_input_tolerated():
    ws = WorkingSet.from_dict({"verify_map": None, "goal": "g"})
    assert ws.verify_map == {}
    assert ws.goal == "g"

    ws = WorkingSet.from_dict({"verify_map": "not-a-dict"})
    assert ws.verify_map == {}

    ws = WorkingSet.from_dict(
        {
            "verify_map": {
                "": ["tests/x.py"],
                "pkg/a.py": [],
                "pkg/b.py": ["", "  "],
                "pkg/c.py": ["tests/test_c.py", ""],
                "pkg/d.py": "tests/test_d.py",
            }
        }
    )
    assert "" not in ws.verify_map
    assert "pkg/a.py" not in ws.verify_map
    assert "pkg/b.py" not in ws.verify_map
    assert ws.verify_map.get("pkg/c.py") == {"tests/test_c.py"}
    assert ws.verify_map.get("pkg/d.py") == {"tests/test_d.py"}


def test_legacy_task_state_without_verify_map():
    """旧快照无 verify_map：不崩溃；不伪造 map 条目。"""
    ws = WorkingSet.from_dict(
        {
            "goal": "legacy",
            "pending_verify": ["verify edit on pkg/a.py"],
            "verify_targets": ["tests/test_a.py"],
        }
    )
    assert ws.verify_map == {}
    assert ws.pending_verify == ["verify edit on pkg/a.py"]
    assert ws.verify_targets == ["tests/test_a.py"]


def test_failure_target_roundtrip(tmp_path):
    ws = WorkingSet(
        goal="g",
        failure_context=[{"file": "a.py", "line": 1}],
        failure_target="tests/test_a.py",
        verify_map={"pkg/a.py": {"tests/test_a.py"}},
    )
    ws._sync_verify_views_from_map()
    _save_task_state(str(tmp_path), ws)
    ws2 = WorkingSet.from_dict(_load_task_state(str(tmp_path)))
    assert ws2.failure_target == "tests/test_a.py"
    assert ws2.failure_context == [{"file": "a.py", "line": 1}]

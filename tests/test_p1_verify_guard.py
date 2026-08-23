"""P1-6: VERIFY_REQUIRED 最小 Runtime guard 契约测试.

待验证状态(verify_targets 非空)下:
- 无关 mutation(编辑非 pending 文件)必须被阻止
- read / diagnostic / test 操作不能被阻止
- 编辑 pending 文件(修复)、undo_last_tx、forge_sync 不能被阻止
- 验证通过(verify_targets 清空)后恢复正常

不测试"权限系统/状态机", 只测最小硬拦截。
"""
from __future__ import annotations

from forge.runtime import Runtime, WorkingSet, _mutation_target_paths


def _pending_ws() -> WorkingSet:
    ws = WorkingSet(goal="g")
    ws.verify_targets = ["tests/test_a.py"]
    ws.pending_verify = ["verify edit on pkg/a.py"]
    ws.verify_map = {"pkg/a.py": {"tests/test_a.py"}}
    return ws


def _rt(ws: WorkingSet) -> Runtime:
    rt = Runtime.__new__(Runtime)
    rt._working_set = ws
    return rt


def _blocked(rt: Runtime, name: str, args: dict) -> bool:
    r = rt._guard_pending_verify(name, args)
    return r is not None and not r.success


# --------------------------------------------------------------------------- #
# 无待验证状态
# --------------------------------------------------------------------------- #


def test_no_guard_when_no_verify_targets():
    ws = WorkingSet(goal="g")
    rt = _rt(ws)
    assert rt._guard_pending_verify("str_replace", {"path": "pkg/b.py"}) is None


def test_no_guard_for_untargeted_pending_only():
    # 有 pending_verify 但无 verify_target(编辑无关联测试) → 不触发 guard
    ws = WorkingSet(goal="g")
    ws.pending_verify = ["verify edit on pkg/lonely.py"]
    ws.verify_targets = []
    rt = _rt(ws)
    assert rt._guard_pending_verify("str_replace", {"path": "pkg/b.py"}) is None


# --------------------------------------------------------------------------- #
# 阻止无关 mutation
# --------------------------------------------------------------------------- #


def test_blocks_unrelated_str_replace():
    rt = _rt(_pending_ws())
    assert _blocked(rt, "str_replace", {"path": "pkg/b.py", "old_string": "x", "new_string": "y"})


def test_blocks_unrelated_write_file():
    rt = _rt(_pending_ws())
    assert _blocked(rt, "write_file", {"path": "pkg/b.py", "content": "x"})


def test_blocks_unrelated_delete_file():
    rt = _rt(_pending_ws())
    assert _blocked(rt, "delete_file", {"path": "pkg/b.py"})


def test_blocks_edit_files_batch_with_unrelated_file():
    rt = _rt(_pending_ws())
    args = {"edits": [{"path": "pkg/a.py", "operations": []}, {"path": "pkg/b.py", "operations": []}]}
    assert _blocked(rt, "edit_files_batch", args)


def test_blocks_apply_patch_on_unrelated_file():
    rt = _rt(_pending_ws())
    args = {"patch": "--- a/pkg/b.py\n+++ b/pkg/b.py\n@@ -1 +1 @@\n-x\n+y\n"}
    assert _blocked(rt, "apply_patch", args)


def test_block_message_guides_to_verify():
    rt = _rt(_pending_ws())
    r = rt._guard_pending_verify("str_replace", {"path": "pkg/b.py"})
    assert r is not None and not r.success
    assert "run_test_structured" in (r.display or "") or "验证" in (r.display or "")


# --------------------------------------------------------------------------- #
# 放行: read / diagnostic / test / 修复 pending 文件
# --------------------------------------------------------------------------- #


def test_allows_read_tools():
    rt = _rt(_pending_ws())
    for name, args in [
        ("read_file", {"path": "pkg/a.py"}),
        ("read_file", {"path": "pkg/b.py"}),
        ("search_code", {"pattern": "x"}),
        ("glob_files", {"pattern": "**/*.py"}),
        ("git_diff", {}),
    ]:
        assert rt._guard_pending_verify(name, args) is None, name


def test_allows_test_and_diagnostic_tools():
    rt = _rt(_pending_ws())
    for name, args in [
        ("run_test_structured", {"target": "tests/test_a.py"}),
        ("run_test_structured", {"target": "tests/"}),
        ("run_command", {"cmd": "pytest -q"}),
        ("run_type_check", {"path": "pkg/a.py"}),
    ]:
        assert rt._guard_pending_verify(name, args) is None, name


def test_allows_mutation_on_pending_path():
    # 编辑正在验证中的文件(修复失败测试)必须放行
    rt = _rt(_pending_ws())
    assert rt._guard_pending_verify("str_replace", {"path": "pkg/a.py", "old_string": "x", "new_string": "y"}) is None
    assert rt._guard_pending_verify("write_file", {"path": "pkg/a.py", "content": "z"}) is None


def test_allows_undo_and_sync():
    rt = _rt(_pending_ws())
    assert rt._guard_pending_verify("undo_last_tx", {}) is None
    assert rt._guard_pending_verify("forge_sync", {}) is None


# --------------------------------------------------------------------------- #
# 验证通过后恢复
# --------------------------------------------------------------------------- #


def test_restores_after_verification_clears_targets():
    ws = _pending_ws()
    rt = _rt(ws)
    # 验证前: 阻止
    assert _blocked(rt, "str_replace", {"path": "pkg/b.py"})
    # 模拟测试通过清空 verify_targets(由 WorkingSet.update_from_tool 完成)
    ws.verify_targets = []
    ws.verify_map = {}
    ws.pending_verify = []
    # 验证后: 恢复正常
    assert rt._guard_pending_verify("str_replace", {"path": "pkg/b.py"}) is None


# --------------------------------------------------------------------------- #
# 路径提取 helper
# --------------------------------------------------------------------------- #


def test_mutation_target_paths_extracts_patch_and_batch():
    assert "pkg/b.py" in _mutation_target_paths(
        "apply_patch",
        {"patch": "--- a/pkg/b.py\n+++ b/pkg/b.py\n@@ -1 +1 @@\n-x\n+y\n"},
    )
    got = _mutation_target_paths(
        "edit_files_batch",
        {"edits": [{"path": "pkg/a.py"}, {"path": "pkg/b.py"}]},
    )
    assert set(got) == {"pkg/a.py", "pkg/b.py"}
    assert _mutation_target_paths("str_replace", {"path": "pkg/c.py"}) == ["pkg/c.py"]
    assert _mutation_target_paths("write_file", {"path": "pkg/c.py"}) == ["pkg/c.py"]

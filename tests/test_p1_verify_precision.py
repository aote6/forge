"""P1-5: pending_verify 精确清账契约测试.

验证闭环里, 一次测试通过只应清除"与本次测试相关"的验证状态, 不能:
- 多文件编辑时, 验证 A 把 B 的 pending_verify 一并清掉
- 测试成功清掉与本次验证无关的 failure_context
- 测试失败丢掉待验证/失败上下文

契约(被测行为):
1. edit 成功 → pending_verify + verify_targets + verify_map 关联记录
2. test 成功(target=T) → 只清被 T 覆盖的 pending/verify_target/failure_context
3. test 失败 → 保留 pending_verify / verify_targets, 记录 failure_context + failure_target
4. 无关联测试的 edit(无 verify_target) → 只有全量(target=tests/等broad)通过才清
"""
from __future__ import annotations

from forge.adapters.base import ToolResult
from forge.runtime import WorkingSet


def _edit_ok(path: str, verify_target: str | None = None) -> ToolResult:
    """构造一次成功的编辑结果(可选带 VERIFY_REQUIRED target)。"""
    payload: dict = {"path": path, "tx_id": 1}
    display = f"RESULT: path={path} tx=1"
    if verify_target:
        payload["verify_target"] = verify_target
        display = (
            f"VERIFY_REQUIRED: run_test_structured(target={verify_target!r}) "
            f"— 验证完成前不要开始无关重构\n"
            + display
        )
    return ToolResult.ok(display=display, payload=payload)


def _edit(ws: WorkingSet, path: str, verify_target: str | None = None) -> None:
    ws.update_from_tool(
        "str_replace",
        {"path": path, "old_string": "x", "new_string": "y"},
        _edit_ok(path, verify_target),
    )


def _run_test(
    ws: WorkingSet,
    target: str,
    *,
    success: bool = True,
    failure_context: list | None = None,
) -> None:
    args = {"target": target}
    if success:
        r = ToolResult.ok(
            display=f"RESULT: pytest target={target} exit=0",
            payload={"returncode": 0, "failed_tests": [], "failure_context": []},
        )
    else:
        fc = failure_context or [{"file": "pkg/a.py", "line": 3, "source": ">> 3: x"}]
        r = ToolResult.fail(
            display=f"FAILED: pytest target={target} exit=1",
            payload={
                "returncode": 1,
                "failed_tests": [f"{target}::test FAILED"],
                "failure_context": fc,
            },
        )
    ws.update_from_tool("run_test_structured", args, r)


def _pending_paths(ws: WorkingSet) -> set[str]:
    return {ws._pending_entry_path(p) for p in ws.pending_verify}


# --------------------------------------------------------------------------- #
# 1. 关联记录
# --------------------------------------------------------------------------- #


def test_edit_records_verify_map_and_targets():
    ws = WorkingSet(goal="g")
    _edit(ws, "pkg/a.py", "tests/test_a.py")
    assert ws.pending_verify
    assert "tests/test_a.py" in ws.verify_targets
    assert ws.verify_map.get("pkg/a.py") == {"tests/test_a.py"}


# --------------------------------------------------------------------------- #
# 2. 成功只清相关状态(多文件)
# --------------------------------------------------------------------------- #


def test_success_clears_only_matching_target():
    ws = WorkingSet(goal="g")
    _edit(ws, "pkg/a.py", "tests/test_a.py")
    _edit(ws, "pkg/b.py", "tests/test_b.py")

    _run_test(ws, "tests/test_a.py", success=True)

    # A 被验证 → 只清 A; B 的待验证必须保留
    assert _pending_paths(ws) == {"pkg/b.py"}
    assert ws.verify_targets == ["tests/test_b.py"]
    assert "pkg/b.py" in ws.verify_map
    assert "pkg/a.py" not in ws.verify_map
    assert not ws.failure_context


def test_success_broad_target_clears_all():
    ws = WorkingSet(goal="g")
    _edit(ws, "pkg/a.py", "tests/test_a.py")
    _edit(ws, "pkg/b.py", "tests/test_b.py")

    _run_test(ws, "tests/", success=True)

    assert ws.pending_verify == []
    assert ws.verify_targets == []
    assert ws.verify_map == {}
    assert not ws.failure_context


def test_success_directory_prefix_covers_nested_target():
    ws = WorkingSet(goal="g")
    _edit(ws, "pkg/a.py", "tests/test_a.py")

    # 跑 tests/ 目录通过 → 覆盖 tests/test_a.py
    _run_test(ws, "tests/", success=True)
    assert _pending_paths(ws) == set()
    assert ws.verify_targets == []


# --------------------------------------------------------------------------- #
# 3. 失败保留上下文
# --------------------------------------------------------------------------- #


def test_failure_preserves_pending_and_records_failure_target():
    ws = WorkingSet(goal="g")
    _edit(ws, "pkg/a.py", "tests/test_a.py")
    _edit(ws, "pkg/b.py", "tests/test_b.py")

    _run_test(
        ws,
        "tests/test_a.py",
        success=False,
        failure_context=[{"file": "pkg/a.py", "line": 3, "source": ">> 3: boom"}],
    )

    # 失败: 两个 pending 都保留, verify_targets 都保留, failure_context 记录
    assert _pending_paths(ws) == {"pkg/a.py", "pkg/b.py"}
    assert set(ws.verify_targets) == {"tests/test_a.py", "tests/test_b.py"}
    assert ws.failure_context
    assert ws.failure_target == "tests/test_a.py"


def test_success_after_failure_clears_only_related_failure_context():
    ws = WorkingSet(goal="g")
    _edit(ws, "pkg/a.py", "tests/test_a.py")
    _edit(ws, "pkg/b.py", "tests/test_b.py")

    # A 先失败
    _run_test(ws, "tests/test_a.py", success=False)
    assert ws.failure_context

    # A 再通过 → 清 A 的 pending + failure_context; B 保留
    _run_test(ws, "tests/test_a.py", success=True)

    assert _pending_paths(ws) == {"pkg/b.py"}
    assert not ws.failure_context
    assert ws.failure_target is None
    assert "pkg/b.py" in ws.verify_map


def test_success_on_b_does_not_clear_a_failure_context():
    ws = WorkingSet(goal="g")
    _edit(ws, "pkg/a.py", "tests/test_a.py")
    _edit(ws, "pkg/b.py", "tests/test_b.py")

    # A 失败(记录 A 的 failure_context)
    _run_test(ws, "tests/test_a.py", success=False)
    a_fc = list(ws.failure_context)

    # B 通过 → 清 B, 但 A 仍待验证, A 的 failure_context 必须保留
    _run_test(ws, "tests/test_b.py", success=True)

    assert _pending_paths(ws) == {"pkg/a.py"}
    assert ws.failure_context == a_fc
    assert ws.failure_target == "tests/test_a.py"
    assert "pkg/a.py" in ws.verify_map
    assert "pkg/b.py" not in ws.verify_map


# --------------------------------------------------------------------------- #
# 4. 无关联测试的 edit → 只被全量通过清除
# --------------------------------------------------------------------------- #


def test_untargeted_edit_cleared_only_by_broad_run():
    ws = WorkingSet(goal="g")
    # 无关联测试 → 无 verify_target
    _edit(ws, "pkg/lonely.py", None)

    assert ws.pending_verify
    assert ws.verify_targets == []

    # 跑一个具体测试(与 lonely.py 无关)通过 → 不能清掉 untargeted pending
    _run_test(ws, "tests/test_other.py", success=True)
    assert _pending_paths(ws) == {"pkg/lonely.py"}

    # 全量通过 → 清掉
    _run_test(ws, "tests/", success=True)
    assert ws.pending_verify == []

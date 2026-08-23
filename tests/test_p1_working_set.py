"""P1-1: task-level Working Set contract tests.

Minimal behavior:
- goal set at task start
- incremental update from real tool results
- files_read / files_edited tracked
- pending_verify / open_hypotheses tracked
- summary stays short (~30 lines) and keeps goal after many tool rounds
"""
from __future__ import annotations

from forge.adapters.base import ToolResult
from forge.runtime import WorkingSet


def test_working_set_goal_at_start():
    ws = WorkingSet(goal="fix bug in runtime.py")
    assert "runtime.py" in ws.goal or "fix" in ws.goal.lower()
    s = ws.summary()
    assert "fix bug" in s or "runtime.py" in s
    assert s.count("\n") < 30


def test_working_set_files_read_on_read_file():
    ws = WorkingSet(goal="inspect foo")
    r = ToolResult.ok(
        display="=== FORGE/read_file ===\nSTATUS: OK\npath: forge/runtime.py\n--- BODY ---\nx",
        payload={"path": "forge/runtime.py", "lines": 10},
    )
    ws.update_from_tool("read_file", {"path": "forge/runtime.py"}, r)
    assert "forge/runtime.py" in ws.files_read
    # idempotent append
    ws.update_from_tool("read_file", {"path": "forge/runtime.py"}, r)
    assert ws.files_read.count("forge/runtime.py") == 1


def test_working_set_files_edited_on_mutation_success():
    ws = WorkingSet(goal="edit bar")
    r = ToolResult.ok(
        display="RESULT: path=forge/runtime.py replacements=1 object_id=1 tx=42 version=3",
        payload={"path": "forge/runtime.py", "tx_id": 42, "version": 3},
    )
    ws.update_from_tool(
        "str_replace",
        {"path": "forge/runtime.py", "old_string": "a", "new_string": "b"},
        r,
    )
    assert "forge/runtime.py" in ws.files_edited
    # pending_verify should mention the edit or path
    assert any("runtime.py" in p for p in ws.pending_verify) or ws.pending_verify


def test_working_set_near_miss_hypothesis():
    ws = WorkingSet(goal="replace string")
    r = ToolResult.fail(
        display=(
            "str_replace failed: old_string not found\n"
            "--- NEAR_MISS candidates ---\n"
            "def foo():\n    return 1"
        ),
        payload={},
    )
    ws.update_from_tool(
        "str_replace",
        {"path": "a.py", "old_string": "x", "new_string": "y"},
        r,
    )
    text = " ".join(ws.open_hypotheses + ws.pending_verify).lower()
    assert "near_miss" in text or "a.py" in text or "old_string" in text


def test_working_set_summary_stable_after_many_updates():
    ws = WorkingSet(goal="P1 goal: keep me visible always")
    for i in range(40):
        path = f"file_{i % 5}.py"
        r = ToolResult.ok(
            display=f"RESULT: path={path} body=" + ("x" * 200),
            payload={"path": path},
        )
        name = "read_file" if i % 2 == 0 else "str_replace"
        ws.update_from_tool(name, {"path": path}, r)
    summary = ws.summary()
    assert "P1 goal: keep me visible always" in summary
    lines = summary.strip().splitlines()
    assert len(lines) <= 32
    # still knows about recent files
    assert any("file_" in ln for ln in lines)


def test_working_set_constraints_and_inject_shape():
    ws = WorkingSet(
        goal="ship P1-1",
        constraints=["do not push remote", "no P1-3"],
    )
    s = ws.summary()
    assert "ship P1-1" in s
    assert "do not push remote" in s or "constraints" in s.lower()

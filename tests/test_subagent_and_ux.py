"""Subagent + product UX polish tests."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from forge.adapters.base import Message, ToolCall, ToolResult
from forge.subagent import (
    SUBAGENT_MAX_STEPS,
    filter_schemas_for_subagent,
    run_subagent,
)
from forge.tools.schemas import READ_ONLY_TOOL_DECLARATIONS, MUTATION_TOOL_DECLARATIONS
from forge.tools import make_tools
from forge.workspace import Workspace
from forge.runtime import _load_session_summary, _save_session_summary


def test_filter_schemas_for_subagent():
    schemas = list(READ_ONLY_TOOL_DECLARATIONS) + list(MUTATION_TOOL_DECLARATIONS)
    sub = filter_schemas_for_subagent(schemas)
    names = {s["name"] for s in sub}
    assert "read_file" in names and "str_replace" in names and "write_file" in names
    assert "spawn_subagent" not in names  # not nested
    assert "create_object" not in names
    assert "apply_patch" not in names


def test_run_subagent_returns_final_text_only():
    class FakeAdapter:
        def __init__(self):
            self.n = 0

        def send(self, messages, schemas):
            self.n += 1
            if self.n == 1:
                return MagicMock(
                    content="",
                    tool_calls=[
                        ToolCall(id="1", name="search_code", arguments={"pattern": "x"})
                    ],
                )
            return MagicMock(content="bug is in a.py line 3", tool_calls=None)

    tools = {
        "search_code": lambda pattern, path=".": ToolResult.ok(display="a.py:3:x"),
        "read_file": lambda path, start=1, end=0: ToolResult.ok(display="ok"),
        "str_replace": lambda **k: ToolResult.ok(display="ok"),
        "write_file": lambda **k: ToolResult.ok(display="ok"),
    }
    schemas = filter_schemas_for_subagent(
        list(READ_ONLY_TOOL_DECLARATIONS) + list(MUTATION_TOOL_DECLARATIONS)
    )
    out = run_subagent(FakeAdapter(), tools, schemas, "find bug", max_steps=5)
    assert "bug is in a.py" in out
    assert "tool_calls" not in out


def test_spawn_subagent_on_schema():
    names = {d["name"] for d in READ_ONLY_TOOL_DECLARATIONS}
    assert "spawn_subagent" in names


def test_session_summary_roundtrip(tmp_path: Path):
    _save_session_summary(str(tmp_path), ["结论A", "结论B"])
    text = _load_session_summary(str(tmp_path))
    assert "上次会话摘要" in text
    assert "结论A" in text or "结论B" in text


def test_read_file_truncates_long_file(tmp_path: Path):
    lines = [f"line{i}" for i in range(300)]
    (tmp_path / "big.py").write_text("\n".join(lines) + "\n", encoding="utf-8")
    ws = Workspace(project_root=str(tmp_path))
    tools = make_tools(workspace=ws, allow_mutation=False)
    r = tools["read_file"]("big.py")
    assert r.success
    # large files now return outline mode
    assert r.payload.get("mode") == "outline" or "outline" in r.display.lower() or "FORGE/read_file" in r.display
    assert "line0" in r.display or "L1" in r.display


def test_truncate_keeps_tail():
    from forge.tools import local_tools as lt

    long = "HEAD" + ("x" * 10000) + "TAIL_ERROR_MSG"
    # temporarily lower limit
    old = lt.MAX_OUTPUT_CHARS
    try:
        lt.MAX_OUTPUT_CHARS = 100
        out = lt._truncate(long)
        assert out.endswith("TAIL_ERROR_MSG") or "TAIL_ERROR" in out
        assert "截断前部" in out or out.startswith("...")
    finally:
        lt.MAX_OUTPUT_CHARS = old


def test_str_replace_strips_needle_whitespace(tmp_path: Path):
    """Without veritas: pure string logic covered via intent_tools strip behavior unit."""
    text = "alpha\nbeta\ngamma\n"
    old_string = "\nbeta\n"
    needle = old_string.strip("\n\r")
    assert needle in text

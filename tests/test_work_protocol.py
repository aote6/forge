"""Work-protocol layer: session_changes, coverage_hint, clarify, stop."""
from __future__ import annotations

from pathlib import Path

from forge.tools.session_changes import record, list_changes, format_list, clear
from forge.tools.related_tests import coverage_hint, format_related_hint
from forge.tools.goal_clarify import needs_clarify, clarification_message, mark_clarified, reset, user_looks_like_clarification
from forge.tools.schemas import READ_ONLY_TOOL_DECLARATIONS
from forge.tools import make_tools
from forge.workspace import Workspace


def test_session_changes_record():
    clear()
    record("a.py", tx_id=1, tool="str_replace", summary="x->y")
    record("b.py", tx_id=2, tool="write_file", summary="new")
    items = list_changes()
    assert len(items) == 2
    assert "a.py" in format_list()


def test_session_changes_tool(tmp_path: Path):
    clear()
    record("x.py", tx_id=9, tool="str_replace", summary="fix")
    ws = Workspace(project_root=str(tmp_path))
    tools, _, _ = make_tools(workspace=ws, allow_mutation=False)
    assert "session_changes" in tools
    r = tools["session_changes"]()
    assert r.success
    assert "x.py" in r.display


def test_coverage_hint_no_tests(tmp_path: Path):
    (tmp_path / "solo.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    h = coverage_hint(str(tmp_path), "solo.py")
    assert "COVERAGE_HINT" in h
    assert "绿" in h or "验证" in h


def test_coverage_hint_with_test(tmp_path: Path):
    (tmp_path / "mod.py").write_text("def process():\n    return 1\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_mod.py").write_text(
        "from mod import process\n\ndef test_process():\n    assert process() == 1\n",
        encoding="utf-8",
    )
    h = format_related_hint(str(tmp_path), "mod.py", symbol_hint="process")
    assert "RELATED_TESTS" in h
    assert "COVERAGE_HINT" in h


def test_goal_clarify_ambiguous():
    reset()
    assert needs_clarify("优化一下这个文件")
    assert not needs_clarify("优化可读性并保持测试通过")
    assert "验收" in clarification_message()
    mark_clarified()
    assert not needs_clarify("再优化一下")


def test_user_clarification_detect():
    reset()
    assert user_looks_like_clarification("只要可读性，测试保持绿")


def test_session_changes_on_schema():
    names = {d["name"] for d in READ_ONLY_TOOL_DECLARATIONS}
    assert "session_changes" in names

"""P1-4: post-edit verification loop contract tests."""
from __future__ import annotations

from forge.adapters.base import ToolResult
from forge.runtime import WorkingSet
from forge.tools.related_tests import find_related_tests, format_related_hint
from forge.tools.intent_tools import _attach_diff


def test_format_related_includes_verify_required_style_hint(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "mod.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_mod.py").write_text(
        "from pkg.mod import f\n\ndef test_f():\n    assert f() == 1\n",
        encoding="utf-8",
    )
    tests = find_related_tests(str(tmp_path), "pkg/mod.py")
    assert any("test_mod" in t for t in tests)
    hint = format_related_hint(str(tmp_path), "pkg/mod.py")
    assert "run_test_structured" in hint
    assert "test_mod" in hint


def test_attach_diff_prepends_verify_required(tmp_path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "mod.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_mod.py").write_text(
        "from pkg.mod import f\n\ndef test_f():\n    assert f() == 1\n",
        encoding="utf-8",
    )
    r = ToolResult.ok(
        display="RESULT: path=pkg/mod.py tx=1",
        payload={
            "path": "pkg/mod.py",
            "tx_id": 1,
            "version": 1,
            "_project_root": str(tmp_path),
            "_edit_symbols": ["f"],
        },
    )
    out = _attach_diff(r, "pkg/mod.py", "def f():\n    return 1\n", "def f():\n    return 2\n")
    assert "VERIFY_REQUIRED" in (out.display or "")
    assert "run_test_structured" in (out.display or "")


def test_attach_diff_no_related_no_verify_required(tmp_path):
    (tmp_path / "lonely.py").write_text("x = 1\n", encoding="utf-8")
    r = ToolResult.ok(
        display="RESULT: path=lonely.py tx=1",
        payload={
            "path": "lonely.py",
            "tx_id": 1,
            "_project_root": str(tmp_path),
        },
    )
    out = _attach_diff(r, "lonely.py", "x = 1\n", "x = 2\n")
    # no related tests → should not force VERIFY_REQUIRED
    assert "VERIFY_REQUIRED" not in (out.display or "")


def test_working_set_records_failure_context():
    ws = WorkingSet(goal="fix after edit")
    ws.pending_verify.append("verify edit on pkg/mod.py")
    r = ToolResult.fail(
        display="FAILED: pytest ...",
        payload={
            "failure_context": [
                {"file": "pkg/mod.py", "line": 3, "source": ">> 3: return 2"}
            ],
            "failed_tests": ["tests/test_mod.py::test_f FAILED"],
            "returncode": 1,
        },
    )
    ws.update_from_tool("run_test_structured", {"target": "tests/test_mod.py"}, r)
    assert ws.failure_context
    assert any("mod.py" in str(c) for c in ws.failure_context)
    # pending_verify should remain (tests failed)
    assert ws.pending_verify


def test_working_set_clears_pending_on_test_success():
    ws = WorkingSet(goal="done")
    ws.pending_verify.append("verify edit on pkg/mod.py")
    ws.failure_context = [{"file": "pkg/mod.py", "line": 1}]
    r = ToolResult.ok(
        display="RESULT: pytest target=tests/ exit=0",
        payload={"returncode": 0, "failed_tests": [], "failure_context": []},
    )
    ws.update_from_tool("run_test_structured", {"target": "tests/"}, r)
    assert ws.pending_verify == [] or not any(
        "verify edit" in p for p in ws.pending_verify
    )
    # failure_context cleared on success
    assert not ws.failure_context


def test_working_set_summary_includes_failure_context():
    ws = WorkingSet(goal="g")
    ws.failure_context = [
        {"file": "a.py", "line": 10, "source": ">> 10: boom"}
    ]
    s = ws.summary()
    assert "failure_context" in s or "a.py" in s

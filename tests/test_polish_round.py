"""Final polish: signature, symbols, projection header, undo cache."""
from __future__ import annotations

from pathlib import Path

from forge.runtime import ToolExecutor
from forge.tools.related_tests import symbols_from_edit, format_related_hint
from forge.tools.intent_tools import _format_projection_results
from forge.tools.tx_shadow import record_tx, undo_last
from forge.tools.read_cache import put, get, clear
from forge.adapters.base import ToolCall, ToolResult


def test_str_replace_signature_ignores_new_string():
    ex = ToolExecutor({})
    a = ex._args_signature("str_replace", {"path": "a.py", "old_string": "x", "new_string": "1"})
    b = ex._args_signature("str_replace", {"path": "a.py", "old_string": "x", "new_string": "2"})
    c = ex._args_signature("str_replace", {"path": "a.py", "old_string": "y", "new_string": "1"})
    assert a == b
    assert a != c


def test_symbols_from_edit():
    syms = symbols_from_edit("def process():\n    return 1\n", "def process():\n    return 2\n")
    assert "process" in syms


def test_projection_header():
    class R:
        def __init__(self, name, success, reason=""):
            self.name = name
            self.success = success
            self.reason = reason
    s = _format_projection_results([R("file", True, "ok")])
    assert "world=ok" in s and "disk=ok" in s
    s2 = _format_projection_results([R("file", False, "boom")])
    assert "disk=FAIL" in s2


def test_undo_invalidates_mental_model(tmp_path: Path):
    clear()
    f = tmp_path / "a.py"
    f.write_text("v1\n", encoding="utf-8")
    put(str(tmp_path), "a.py", "v1\n")
    assert get(str(tmp_path), "a.py") is not None
    record_tx(str(tmp_path), 1, 1, {"a.py": "v1\n"})
    f.write_text("v2\n", encoding="utf-8")
    info = undo_last(str(tmp_path))
    assert info["ok"]
    assert f.read_text() == "v1\n"

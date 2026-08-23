"""P1-3: stronger str_replace failure feedback contract tests."""
from __future__ import annotations

from forge.tools.near_miss import (
    find_near_misses,
    diagnose_mismatch,
    suggest_old_string,
    find_occurrence_lines,
)


def test_diagnose_whitespace_only_difference():
    file_text = "def foo():\n    return 1\n"
    # model sent with different trailing spaces / indent nuance after strip
    old = "def foo():\n  return 1\n"  # 2-space indent vs 4-space
    info = diagnose_mismatch(file_text, old)
    assert info is not None
    kinds = info.get("kinds") or []
    assert "indent" in kinds or "whitespace" in kinds
    assert "hint" in info


def test_diagnose_quote_only_difference():
    file_text = 'msg = "hello"\n'
    old = "msg = 'hello'\n"
    info = diagnose_mismatch(file_text, old)
    assert info is not None
    kinds = info.get("kinds") or []
    assert "quotes" in kinds


def test_suggest_old_string_unique_fuzzy():
    file_text = (
        "class A:\n"
        "    def run(self):\n"
        "        return 42\n"
        "\n"
        "class B:\n"
        "    pass\n"
    )
    old = "def run(self):\n    return 0\n"  # wrong indent + wrong return
    suggestion = suggest_old_string(file_text, old)
    assert suggestion is not None
    assert "line" in suggestion
    assert "text" in suggestion
    # suggested text must actually appear in file
    assert suggestion["text"] in file_text
    assert suggestion["line"] >= 1


def test_find_occurrence_lines_multi():
    file_text = "x = 1\ny = 2\nx = 1\nz = 3\nx = 1\n"
    lines = find_occurrence_lines(file_text, "x = 1")
    assert lines == [1, 3, 5]
    assert len(lines[:3]) == 3


def test_find_near_misses_still_returns_list():
    text = "def foo():\n    return 1\n\ndef bar():\n    return 2\n"
    misses = find_near_misses(text, "def foo():\n    return 0")
    assert isinstance(misses, list)
    assert misses

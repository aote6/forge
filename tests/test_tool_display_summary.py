"""Batch 1: summarize_tool_display — pure unit tests."""
from __future__ import annotations

from forge.terminal_present import (
    MAX_SUMMARY_CHARS,
    MAX_SUMMARY_LINES,
    summarize_tool_display,
)


def test_empty_string():
    assert summarize_tool_display("", success=True) == ""
    assert summarize_tool_display("   \n  ", success=False) == ""


def test_short_output_kept_whole():
    text = "\n".join(f"line {i}" for i in range(5))
    assert summarize_tool_display(text, success=True) == text
    assert summarize_tool_display(text, success=False) == text


def test_long_success_keeps_head_and_tail():
    lines = [f"L{i:03d}" for i in range(40)]
    text = "\n".join(lines)
    out = summarize_tool_display(text, success=True)
    assert "L000" in out
    assert "L001" in out
    assert "L039" in out
    assert "L038" in out
    assert "省略" in out
    assert "last" in out
    # middle content should not all be present
    assert "L020" not in out or out.count("\n") <= MAX_SUMMARY_LINES + 2


def test_long_failure_keeps_tail_error():
    head = [f"stdout {i}" for i in range(50)]
    tail = [
        "test_c ... FAILED",
        "Traceback (most recent call last):",
        '  File "t.py", line 1, in <module>',
        "AssertionError: expected 1 got 2",
        "FAILED",
    ]
    text = "\n".join(head + tail)
    out = summarize_tool_display(text, success=False)
    assert "AssertionError: expected 1 got 2" in out
    assert "FAILED" in out
    assert "Traceback" in out
    assert "省略" in out or "stdout 0" in out  # marker or optional head
    # Must not be head-only dump of the first 16 lines without error
    assert "AssertionError" in out


def test_char_budget_prefers_tail_on_failure():
    # Few lines but each huge; failure must still surface the ending marker.
    huge = "A" * 800
    text = "\n".join([huge, huge, "PREFIX_OK", "FINAL_ERROR: boom"])
    out = summarize_tool_display(text, success=False)
    assert "FINAL_ERROR: boom" in out
    assert len(out) <= MAX_SUMMARY_CHARS + 50  # small slack for markers only if any


def test_line_and_char_limits_together_success():
    lines = [f"row{i}-" + ("x" * 80) for i in range(30)]
    text = "\n".join(lines)
    out = summarize_tool_display(text, success=True)
    assert len(out) <= MAX_SUMMARY_CHARS + 20
    assert "row0-" in out or "省略" in out
    assert "row29-" in out


def test_unicode_chinese_and_emoji():
    lines = [f"中文行{i} 🚀" for i in range(30)]
    text = "\n".join(lines)
    out = summarize_tool_display(text, success=True)
    assert "中文行0" in out
    assert "中文行29" in out
    assert "🚀" in out
    assert "省略" in out


def test_failure_does_not_lose_error_to_second_slice():
    """Regression: old body[:1200] after head-lines dropped tail ERROR."""
    lines = ["noise " + str(i) for i in range(100)]
    lines.append("ERROR: the real failure reason is here")
    text = "\n".join(lines)
    out = summarize_tool_display(text, success=False)
    assert "ERROR: the real failure reason is here" in out

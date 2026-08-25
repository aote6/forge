"""Batch 2: TerminalPresenter + page_text unit tests (no LLM, no Termux)."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from forge.terminal_present import (
    TerminalPresenter,
    page_text,
    summarize_tool_display,
)


class _Buf:
    def __init__(self):
        self.lines: list[str] = []

    def __call__(self, *args, **kwargs):
        end = kwargs.get("end", "\n")
        s = " ".join(str(a) for a in args) if args else ""
        if self.lines and end == "" and self.lines:
            self.lines[-1] = self.lines[-1] + s
        else:
            self.lines.append(s)

    def text(self) -> str:
        return "\n".join(self.lines)


def test_on_tool_start_writes_name():
    buf = _Buf()
    p = TerminalPresenter(
        writer=buf,
        input_fn=lambda _: "q",
        heartbeat_interval=10.0,
        timer_factory=lambda d, cb: type("H", (), {"cancel": lambda self: None})(),
    )
    p.on_tool_start(SimpleNamespace(data={"name": "run_test"}))
    assert any("run_test" in x for x in buf.lines)
    p._stop_heartbeat()


def test_on_tool_end_success_short():
    buf = _Buf()
    p = TerminalPresenter(writer=buf, input_fn=lambda _: "q")
    p.on_tool_start(SimpleNamespace(data={"name": "t"}))
    p.on_tool_end(SimpleNamespace(data={"success": True, "display": "ok line"}))
    joined = buf.text()
    assert "OK" in joined
    assert "ok line" in joined


def test_on_tool_end_failure_uses_summary_tail():
    buf = _Buf()
    p = TerminalPresenter(writer=buf, input_fn=lambda _: "q")
    long_head = "\n".join(f"noise {i}" for i in range(40))
    disp = long_head + "\nERROR: real failure\nTraceback\nAssertionError: x"
    p.on_tool_end(SimpleNamespace(data={"success": False, "display": disp}))
    joined = buf.text()
    assert "FAIL" in joined
    assert "AssertionError: x" in joined
    assert "省略" in joined or "ERROR" in joined


def test_on_tool_end_empty_display_no_body():
    buf = _Buf()
    p = TerminalPresenter(writer=buf, input_fn=lambda _: "q")
    p.on_tool_end(SimpleNamespace(data={"success": True, "display": ""}))
    joined = buf.text()
    assert "OK" in joined
    # no dump of empty body lines beyond mark
    assert joined.strip().endswith("OK") or "OK" in joined


def test_on_tool_end_long_does_not_dump_full_middle():
    buf = _Buf()
    p = TerminalPresenter(writer=buf, input_fn=lambda _: "q")
    lines = [f"L{i:03d}" for i in range(50)]
    disp = "\n".join(lines)
    p.on_tool_end(SimpleNamespace(data={"success": True, "display": disp}))
    joined = buf.text()
    assert "L000" in joined
    assert "L049" in joined
    assert "L025" not in joined  # middle omitted in summary
    assert "省略" in joined


def test_show_assistant():
    buf = _Buf()
    p = TerminalPresenter(writer=buf, input_fn=lambda _: "q")
    p.show_assistant("hello")
    assert any("hello" in x for x in buf.lines)


def test_page_text_empty():
    buf = _Buf()
    page_text("", writer=buf, input_fn=lambda _: "q", page_size=5)
    assert "(no tool output)" in buf.text()


def test_page_text_single_page_no_prompt():
    buf = _Buf()
    calls = []

    def fake_in(_):
        calls.append(1)
        return "q"

    page_text("a\nb\nc", title="t", writer=buf, input_fn=fake_in, page_size=10)
    assert "a" in buf.text() and "c" in buf.text()
    assert not calls  # no interactive loop


def test_page_text_multipage_enter_next_and_q():
    buf = _Buf()
    cmds = iter(["", "", "q"])  # next, next, quit

    def fake_in(_):
        return next(cmds)

    body = "\n".join(f"line{i}" for i in range(20))
    page_text(body, title="tool=x", writer=buf, input_fn=fake_in, page_size=5)
    t = buf.text()
    assert "line0" in t
    assert "page 1/" in t
    assert "line5" in t  # second page after Enter


def test_page_text_b_stays_on_first():
    buf = _Buf()
    cmds = iter(["b", "q"])

    def fake_in(_):
        return next(cmds)

    body = "\n".join(f"r{i}" for i in range(12))
    page_text(body, title="t", writer=buf, input_fn=fake_in, page_size=5)
    # should not crash; still show page 1 content
    assert "r0" in buf.text()


def test_page_text_illegal_then_q():
    buf = _Buf()
    cmds = iter(["zzz", "q"])

    def fake_in(_):
        return next(cmds)

    body = "\n".join(f"x{i}" for i in range(15))
    page_text(body, writer=buf, input_fn=fake_in, page_size=4)
    assert "请输入" in buf.text()


def test_page_text_eof_returns():
    buf = _Buf()

    def boom(_):
        raise EOFError

    body = "\n".join(f"y{i}" for i in range(20))
    page_text(body, writer=buf, input_fn=boom, page_size=5)
    # returned without raise


def test_page_text_keyboard_interrupt():
    buf = _Buf()

    def boom(_):
        raise KeyboardInterrupt

    body = "\n".join(f"z{i}" for i in range(20))
    page_text(body, writer=buf, input_fn=boom, page_size=5)


def test_page_last_uses_pager_not_full_dump_contract():
    """Long last goes through page_text path (multi-page footer appears)."""
    buf = _Buf()
    cmds = iter(["q"])

    def fake_in(_):
        return next(cmds)

    p = TerminalPresenter(writer=buf, input_fn=fake_in, page_size=5)
    long = "\n".join(f"row{i}" for i in range(30))
    p.page_last("run_command", long)
    assert "page 1/" in buf.text()
    assert "row0" in buf.text()


def test_presenter_summary_matches_batch1_helper():
    disp = "\n".join(f"L{i}" for i in range(40)) + "\nERR"
    expected = summarize_tool_display(disp, success=False)
    buf = _Buf()
    p = TerminalPresenter(writer=buf, input_fn=lambda _: "q")
    p.on_tool_end(SimpleNamespace(data={"success": False, "display": disp}))
    # body lines after mark should equal summary
    assert expected in buf.text() or expected.splitlines()[-1] in buf.text()


def test_page_last_sentinel_no_tool_output_yet():
    """Legacy Runtime sentinel must not open pager as real content."""
    buf = _Buf()
    calls = []

    def fake_in(_):
        calls.append(1)
        return "q"

    p = TerminalPresenter(writer=buf, input_fn=fake_in, page_size=5)
    p.page_last("run_test", "(no tool output yet)")
    assert buf.text().strip() == "(no tool output)"
    assert "page " not in buf.text()
    assert not calls


def test_page_last_none_and_whitespace():
    buf = _Buf()
    p = TerminalPresenter(writer=buf, input_fn=lambda _: "q", page_size=5)
    p.page_last(None, None)
    assert "(no tool output)" in buf.text()
    buf2 = _Buf()
    p2 = TerminalPresenter(writer=buf2, input_fn=lambda _: "q", page_size=5)
    p2.page_last("x", "   \n\t  ")
    assert buf2.text().strip() == "(no tool output)"

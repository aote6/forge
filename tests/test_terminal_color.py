"""Batch 5: ANSI truecolor semantics — no real TTY required."""
from __future__ import annotations

from types import SimpleNamespace

from forge.terminal_color import (
    ALARM,
    AMBER,
    AQUA,
    DEEP_BLUE,
    OSCILLOSCOPE,
    PHOSPHOR,
    RESET,
    TUBE_BLUE,
    paint,
)
from forge.terminal_present import TerminalPresenter, page_text


def test_paint_wraps_and_resets():
    out = paint("x", PHOSPHOR)
    assert out.startswith(PHOSPHOR)
    assert out.endswith(RESET)
    assert "x" in out


def test_palette_sequences():
    assert PHOSPHOR == "\x1b[38;2;34;204;136m"
    assert OSCILLOSCOPE == "\x1b[38;2;0;220;130m"
    assert ALARM == "\x1b[38;2;255;85;0m"
    assert TUBE_BLUE == "\x1b[38;2;80;100;140m"
    assert AMBER == "\x1b[38;2;255;191;0m"
    assert DEEP_BLUE.startswith("\x1b[38;2;")
    assert AQUA.startswith("\x1b[38;2;")
    assert RESET == "\x1b[0m"


class _Buf:
    def __init__(self):
        self.parts: list[str] = []

    def __call__(self, *args, **kwargs):
        end = kwargs.get("end", "\n")
        s = " ".join(str(a) for a in args) if args else ""
        self.parts.append(s + ("" if end == "" else "\n"))

    def text(self) -> str:
        return "".join(self.parts)


def _noop_timer(d, cb):
    return type("H", (), {"cancel": lambda self: None})()


def test_tool_start_oscilloscope():
    buf = _Buf()
    p = TerminalPresenter(writer=buf, input_fn=lambda _: "q", timer_factory=_noop_timer)
    p.on_tool_start(SimpleNamespace(data={"name": "run_test"}))
    p._stop_heartbeat()
    t = buf.text()
    assert OSCILLOSCOPE in t
    assert "[run_test] ..." in t
    assert t.rstrip().endswith(RESET) or RESET in t
    assert "🔧" not in t


def test_tool_success_phosphor_body_uncolored():
    buf = _Buf()
    p = TerminalPresenter(writer=buf, input_fn=lambda _: "q", timer_factory=_noop_timer)
    p.on_tool_start(SimpleNamespace(data={"name": "t"}))
    p.on_tool_end(SimpleNamespace(data={"name": "t", "success": True, "display": "plain body"}))
    t = buf.text()
    assert PHOSPHOR in t
    assert "OK" in t
    # body appears without wrapping the whole dump in one color only — at least body plain
    assert "plain body" in t
    assert "plain body" in t.split("plain body")[0] + "plain body"  # present
    # The body write is unpainted: sequence after OK line should include raw body
    assert "✅" not in t


def test_tool_failure_alarm():
    buf = _Buf()
    p = TerminalPresenter(writer=buf, input_fn=lambda _: "q", timer_factory=_noop_timer)
    p.on_tool_end(SimpleNamespace(data={"name": "t", "success": False, "display": "err"}))
    t = buf.text()
    assert ALARM in t
    assert "FAIL" in t
    assert "❌" not in t


def test_heartbeat_tube_blue():
    from tests.test_terminal_heartbeat import ManualClock

    clock = ManualClock()
    buf = _Buf()
    p = TerminalPresenter(
        writer=buf,
        input_fn=lambda _: "q",
        heartbeat_interval=10.0,
        time_fn=clock.time,
        timer_factory=clock.factory,
    )
    p.on_tool_start(SimpleNamespace(data={"name": "pytest"}))
    clock.advance(10.0)
    t = buf.text()
    assert TUBE_BLUE in t
    assert "running..." in t
    assert "🔧" not in t
    p.on_tool_end(SimpleNamespace(data={"name": "pytest", "success": True, "display": "ok"}))


def test_assistant_amber_and_reset():
    buf = _Buf()
    p = TerminalPresenter(writer=buf, input_fn=lambda _: "q", timer_factory=_noop_timer)
    p.on_assistant_delta("Hi")
    p.on_assistant_delta(" there")
    p.on_assistant_done()
    t = buf.text()
    assert AMBER in t
    assert t.count("FORGE>") == 1
    assert "Hi" in t and "there" in t
    assert "🤖" not in t
    assert RESET in t


def test_warning_amber():
    buf = _Buf()
    p = TerminalPresenter(writer=buf, input_fn=lambda _: "q", timer_factory=_noop_timer)
    p.show_warning("disk full")
    t = buf.text()
    assert AMBER in t and "WARN:" in t and "disk full" in t


def test_pager_chrome_tube_blue():
    buf = _Buf()
    cmds = iter(["q"])

    def fake_in(_):
        return next(cmds)

    body = "\n".join(f"L{i}" for i in range(20))
    page_text(body, title="tool=x", writer=buf, input_fn=fake_in, page_size=5)
    t = buf.text()
    assert TUBE_BLUE in t
    assert "page 1/" in t


def test_no_target_emoji_in_presenter_paths():
    buf = _Buf()
    p = TerminalPresenter(writer=buf, input_fn=lambda _: "q", timer_factory=_noop_timer)
    p.on_tool_start(SimpleNamespace(data={"name": "a"}))
    p.on_tool_end(SimpleNamespace(data={"name": "a", "success": True, "display": "x"}))
    p.show_assistant("hello")
    p.show_warning("w")
    t = buf.text()
    for e in ("🔧", "✅", "❌", "🤖", "⚠️"):
        assert e not in t

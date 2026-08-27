"""Terminal presentation helpers.

Currently includes:
  - tool-output summary (summarize_tool_display)
  - minimal pager (page_text)
  - TerminalPresenter (CLI event → terminal display)
  - light tool-running heartbeat (Batch 3)

LLM streaming and full TUI are later batches — not implemented here.
ANSI truecolor via forge.terminal_color (Batch 5). No rich/textual/curses; standard library only.
"""
from __future__ import annotations

import shutil
import threading
import time
from typing import Any, Callable, Iterable, Optional

from forge.terminal_color import (
    ALARM,
    AMBER,
    OSCILLOSCOPE,
    PHOSPHOR,
    TUBE_BLUE,
    paint,
)

MAX_SUMMARY_LINES = 16
MAX_SUMMARY_CHARS = 1200
HEAD_LINES = 4
TAIL_LINES = 12
DEFAULT_PAGE_LINES = 14
HEARTBEAT_INTERVAL = 10.0

_OMIT_TMPL = "…（省略 {n} 行，输入 last 看全文）"

Writer = Callable[..., None]
InputFn = Callable[[str], str]


# ---------------------------------------------------------------------------
# Batch 1 — summary
# ---------------------------------------------------------------------------


def summarize_tool_display(display: str, *, success: bool) -> str:
    """Summarize tool display for the terminal without flooding the screen.

    - Short outputs: kept whole.
    - Long success: small head + larger tail, with an omit marker.
    - Failure (success=False): tail-first so traceback / ERROR stay visible.
    - Character budget never trims away the protected tail region.
    """
    if not display:
        return ""

    text = display if isinstance(display, str) else str(display)
    if not text.strip():
        return ""

    lines = text.splitlines()
    n = len(lines)
    full = "\n".join(lines)

    if n <= MAX_SUMMARY_LINES and len(full) <= MAX_SUMMARY_CHARS:
        return full

    if success:
        return _summarize_success(lines)
    return _summarize_failure(lines)


def _omit_marker(omitted: int) -> str:
    return _OMIT_TMPL.format(n=max(0, omitted))


def _join_fit(parts: list[str], *, max_chars: int) -> str:
    """Join lines; if over budget, drop from the front (protects tail)."""
    if not parts:
        return ""
    body = "\n".join(parts)
    if len(body) <= max_chars:
        return body
    start = 0
    while start < len(parts) - 1:
        cand = "\n".join(parts[start:])
        if len(cand) <= max_chars:
            return cand
        start += 1
    last = parts[-1]
    if len(last) <= max_chars:
        return last
    if max_chars <= 3:
        return last[-max_chars:]
    return "…" + last[-(max_chars - 1) :]


def _summarize_success(lines: list[str]) -> str:
    n = len(lines)
    head_n = min(HEAD_LINES, n)
    tail_n = min(TAIL_LINES, max(0, n - head_n))
    if head_n + tail_n >= n:
        return _join_fit(lines, max_chars=MAX_SUMMARY_CHARS)

    omitted = n - head_n - tail_n
    head = lines[:head_n]
    tail = lines[-tail_n:] if tail_n else []
    marker = _omit_marker(omitted)
    parts = head + [marker] + tail
    return _join_fit(parts, max_chars=MAX_SUMMARY_CHARS)


def _summarize_failure(lines: list[str]) -> str:
    n = len(lines)
    if n == 0:
        return ""

    take = min(MAX_SUMMARY_LINES, n)
    tail = lines[-take:]
    omitted = n - take

    if omitted <= 0:
        return _join_fit(tail, max_chars=MAX_SUMMARY_CHARS)

    marker = _omit_marker(omitted)
    parts = [marker] + tail
    body = _join_fit(parts, max_chars=MAX_SUMMARY_CHARS)
    if omitted > 0 and lines:
        head_line = lines[0]
        trial = head_line + "\n" + body
        if len(trial) <= MAX_SUMMARY_CHARS and body.count("\n") + 1 < MAX_SUMMARY_LINES:
            if marker in body:
                return trial
    return body


# ---------------------------------------------------------------------------
# Batch 2 — pager + presenter
# ---------------------------------------------------------------------------


def page_lines_default() -> int:
    try:
        cols, rows = shutil.get_terminal_size(fallback=(80, 24))
        # leave room for footer + prompt
        return max(8, min(40, int(rows) - 4))
    except Exception:
        return DEFAULT_PAGE_LINES


def page_text(
    text: str,
    *,
    title: str = "",
    writer: Optional[Writer] = None,
    input_fn: Optional[InputFn] = None,
    page_size: Optional[int] = None,
) -> None:
    """Minimal confirmation-driven pager (Enter / b / q). No raw mode.

    - Empty → one-line notice, return.
    - Fits in one page → print once, return (no interactive loop).
    - Multi-page → interactive; EOF / KeyboardInterrupt exits safely to caller.
    """
    write = writer or print
    ask = input_fn or input
    size = page_size if page_size and page_size > 0 else page_lines_default()

    if text is None:
        text = ""
    raw = text if isinstance(text, str) else str(text)
    if not raw.strip():
        write("(no tool output)")
        return

    lines = raw.splitlines() or [raw]
    total = len(lines)
    if total <= size:
        if title:
            write(f"📋 {title}")
        write("\n".join(lines))
        return

    n_pages = (total + size - 1) // size
    page = 0

    def _show(p: int) -> None:
        start = p * size
        end = min(start + size, total)
        chunk = lines[start:end]
        label = title or "output"
        write("\n".join(chunk))
        write(
            paint(
                f"── {label}  page {p + 1}/{n_pages} ── "
                f"Enter:next  b:back  q:return ──",
                TUBE_BLUE,
            )
        )

    _show(page)
    while True:
        try:
            raw_in = ask("")
        except EOFError:
            write("")  # clear line of control
            return
        except KeyboardInterrupt:
            write("")
            return

        cmd = (raw_in or "").strip().lower()
        if cmd in ("",):  # Enter → next
            if page >= n_pages - 1:
                # last page: Enter ends pager (explicit, testable)
                return
            page += 1
            _show(page)
            continue
        if cmd == "q":
            return
        if cmd == "b":
            if page > 0:
                page -= 1
            _show(page)
            continue
        write("请输入 Enter(下一页) / b(上一页) / q(返回)")



def _default_timer_factory(delay: float, callback: Callable[[], None]) -> threading.Timer:
    """threading.Timer wrapper: daemon, auto-start, cancel()-able."""
    timer = threading.Timer(delay, callback)
    timer.daemon = True
    timer.start()
    return timer


class TerminalPresenter:
    """Thin terminal presentation layer for the CLI REPL.

    Consumes Runtime tool events and last-tool text. Does not call tools,
    adapters, or mutate Runtime / ToolResult.

    Heartbeat (Batch 3): after HEARTBEAT_INTERVAL without TOOL_CALL_END, print a
    running line. Runtime currently runs tools sequentially (START→execute→END),
    so one active heartbeat lifecycle is enough.
    """

    def __init__(
        self,
        writer: Optional[Writer] = None,
        input_fn: Optional[InputFn] = None,
        page_size: Optional[int] = None,
        *,
        heartbeat_interval: float = HEARTBEAT_INTERVAL,
        time_fn: Optional[Callable[[], float]] = None,
        timer_factory: Optional[Callable[[float, Callable[[], None]], Any]] = None,
    ):
        self._write = writer or print
        self._input = input_fn or input
        self._page_size = page_size  # None → dynamic
        self._heartbeat_interval = float(heartbeat_interval)
        self._time = time_fn or time.monotonic
        # timer_factory(delay_seconds, callback) -> handle with .cancel(); auto-starts
        self._timer_factory = timer_factory or _default_timer_factory

        self._hb_lock = threading.Lock()
        self._hb_token = 0  # bumped on each start/stop; ticks ignore stale tokens
        self._hb_timer: Any = None
        self._hb_name: Optional[str] = None
        self._hb_t0: Optional[float] = None
        self._assistant_open = False
        self._assistant_streamed = False
        # Confirm exclusive: when True, no presenter output (heartbeat / tool /
        # assistant) may touch the terminal. Confirm input owns stdout/stdin.
        self._exclusive = False
        self._exclusive_lock = threading.Lock()

    def begin_exclusive(self) -> None:
        """Enter confirm-exclusive mode: stop all async/presenter writes."""
        self._stop_heartbeat()
        # Close assistant stream state without writing (terminal belongs to confirm).
        self._assistant_open = False
        self._assistant_streamed = False
        with self._exclusive_lock:
            self._exclusive = True

    def end_exclusive(self) -> None:
        """Leave confirm-exclusive mode; subsequent tool events may display."""
        with self._exclusive_lock:
            self._exclusive = False

    def exclusive_terminal(self):
        """Context manager: terminal owned solely by confirm input."""
        from contextlib import contextmanager

        @contextmanager
        def _cm():
            self.begin_exclusive()
            try:
                yield self
            finally:
                self.end_exclusive()

        return _cm()

    def _in_exclusive(self) -> bool:
        with self._exclusive_lock:
            return self._exclusive

    def _emit(self, *args, **kwargs) -> None:
        """Write to terminal unless confirm-exclusive is active."""
        if self._in_exclusive():
            return
        try:
            self._write(*args, **kwargs)
        except Exception:
            pass

    def _stop_heartbeat(self) -> None:
        """Invalidate any in-flight tick and cancel the pending timer."""
        with self._hb_lock:
            self._hb_token += 1
            timer = self._hb_timer
            self._hb_timer = None
            self._hb_name = None
            self._hb_t0 = None
        if timer is not None:
            try:
                timer.cancel()
            except Exception:
                pass

    def _schedule_heartbeat(self, token: int) -> None:
        interval = self._heartbeat_interval
        if interval <= 0:
            return

        def tick() -> None:
            with self._hb_lock:
                if token != self._hb_token or self._hb_name is None or self._hb_t0 is None:
                    return
                name = self._hb_name
                t0 = self._hb_t0
            try:
                elapsed = max(0, int(self._time() - t0))
            except Exception:
                elapsed = 0
            # New line: reliable on Termux; avoid \r redraw games.
            # Confirm exclusive owns the terminal — never write running... there.
            if self._in_exclusive():
                return
            try:
                self._emit(paint(f"\n[{name}] running... {elapsed}s", TUBE_BLUE), flush=True)
            except Exception:
                return
            with self._hb_lock:
                if token != self._hb_token:
                    return
                try:
                    self._hb_timer = self._timer_factory(interval, tick)
                except Exception:
                    self._hb_timer = None

        with self._hb_lock:
            if token != self._hb_token:
                return
            try:
                self._hb_timer = self._timer_factory(interval, tick)
            except Exception:
                self._hb_timer = None

    def on_tool_start(self, event) -> None:
        data = getattr(event, "data", None) or {}
        name = data.get("name") or "?"
        # During confirm exclusive the terminal is owned by input — drop display
        # and do not arm heartbeat (events should not arrive here, but be safe).
        if self._in_exclusive():
            self._stop_heartbeat()
            return
        # Defensive: end any previous heartbeat (Runtime is sequential, but
        # missing END must not leak a timer into the next tool).
        self._stop_heartbeat()
        with self._hb_lock:
            self._hb_token += 1
            token = self._hb_token
            self._hb_name = name
            self._hb_t0 = self._time()
            self._hb_timer = None
        self.on_assistant_done()
        self._assistant_streamed = False
        # spawn_subagent is a long outer shell; clearer than bare "..."
        if name == "spawn_subagent":
            label = "子任务开始"
        else:
            label = "..."
        self._emit(paint(f"\n[{name}] {label}", OSCILLOSCOPE), flush=True)
        self._schedule_heartbeat(token)

    def on_tool_end(self, event) -> None:
        # Capture duration before killing heartbeat state.
        with self._hb_lock:
            t0 = self._hb_t0
            hb_name = self._hb_name
        elapsed_s = None
        if t0 is not None:
            try:
                elapsed_s = max(0, int(self._time() - t0))
            except Exception:
                elapsed_s = None
        # First: kill heartbeat so no tick can print after the mark.
        self._stop_heartbeat()
        data = getattr(event, "data", None) or {}
        ok = bool(data.get("success"))
        name = data.get("name") or hb_name or "?"
        if elapsed_s is not None:
            status = f"[{name}] OK {elapsed_s}s" if ok else f"[{name}] FAIL {elapsed_s}s"
        else:
            status = f"[{name}] OK" if ok else f"[{name}] FAIL"
        if self._in_exclusive():
            return
        color = PHOSPHOR if ok else ALARM
        self._emit(paint(f"\n{status}", color), flush=True)
        disp = (data.get("display") or "").strip()
        if not disp:
            return
        body = summarize_tool_display(disp, success=ok)
        if body:
            # Tool body is uncolored chrome boundary: plain text only.
            self._emit(body, flush=True)

    def on_assistant_delta(self, text: str) -> None:
        """Append assistant text incrementally (one FORGE> prefix per reply)."""
        if not text:
            return
        if self._in_exclusive():
            return
        if not self._assistant_open:
            self._emit(paint("\nFORGE> ", AMBER), end="", flush=True)
            self._assistant_open = True
            self._assistant_streamed = True
        # Stream body in amber without re-prefix; each write still resets.
        self._emit(paint(text, AMBER), end="", flush=True)

    def on_assistant_done(self) -> None:
        """Close the current streamed assistant line if open."""
        if self._assistant_open:
            if not self._in_exclusive():
                self._emit("", flush=True)  # newline after stream
            self._assistant_open = False

    def show_assistant(self, text: str, force: bool = False) -> None:
        """Non-stream path: print full assistant text once.

        If this turn already streamed deltas, skip to avoid double output.
        Callers with structured content that was not streamed (e.g.
        submit_plan plan body) pass force=True.
        """
        if self._assistant_streamed or self._assistant_open:
            self.on_assistant_done()
            self._assistant_streamed = False
            if not force:
                return
        if not text:
            return
        self._emit(paint(f"\nFORGE> {text}", AMBER), flush=True)
        self._assistant_streamed = False

    def show_warning(self, message: str) -> None:
        """UI warning line (amber). Not a ToolResult channel."""
        if not message:
            return
        self._emit(paint(f"\nWARN: {message}", AMBER), flush=True)

    def page_last(self, name: str | None, display: str | None) -> None:
        """Open pager on the latest tool display (from Runtime cache).

        Empty / whitespace / legacy sentinel "(no tool output yet)" all map to a
        single notice and do not enter the pager.
        """
        title = f"last tool={name or ''}".strip()
        if display is None:
            raw = ""
        else:
            raw = display if isinstance(display, str) else str(display)
        stripped = raw.strip()
        if not stripped or stripped == "(no tool output yet)":
            self._write("(no tool output)")
            return
        page_text(
            raw,
            title=title,
            writer=self._write,
            input_fn=self._input,
            page_size=self._page_size,
        )

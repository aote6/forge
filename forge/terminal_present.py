"""Terminal presentation helpers.

Currently includes:
  - tool-output summary (summarize_tool_display)
  - minimal pager (page_text)
  - TerminalPresenter (CLI event → terminal display)

Heartbeat, LLM streaming, and full TUI are later batches — not implemented here.
No rich/textual/curses; standard library only.
"""
from __future__ import annotations

import shutil
from typing import Callable, Optional

Writer = Callable[..., None]
InputFn = Callable[..., str]
DEFAULT_PAGE_LINES = 14

MAX_SUMMARY_LINES = 16
MAX_SUMMARY_CHARS = 1200
HEAD_LINES = 4
TAIL_LINES = 12

_OMIT_TMPL = "…（省略 {n} 行，输入 last 看全文）"


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
    # Preserve intentional leading/trailing content for summary logic, but
    # callers typically already .strip(); empty after strip → nothing to show.
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
    """Join lines with newlines, preferring to keep the *end* of the list.

    parts are ordered top→bottom as they should appear. If over budget,
    drop from the front (except we never drop a lone marker-only edge case
    in a destructive way — caller builds parts carefully).
    """
    if not parts:
        return ""
    body = "\n".join(parts)
    if len(body) <= max_chars:
        return body
    # Drop leading lines until under budget (protects tail).
    start = 0
    while start < len(parts) - 1:
        cand = "\n".join(parts[start:])
        if len(cand) <= max_chars:
            return cand
        start += 1
    # Single remaining line still too long: keep its tail characters.
    last = parts[-1]
    if len(last) <= max_chars:
        return last
    # Leave room for a tiny prefix marker if we slice hard.
    if max_chars <= 3:
        return last[-max_chars:]
    return "…" + last[-(max_chars - 1) :]


def _summarize_success(lines: list[str]) -> str:
    n = len(lines)
    head_n = min(HEAD_LINES, n)
    tail_n = min(TAIL_LINES, max(0, n - head_n))
    # If everything fits in head+tail without omit, just show all (shouldn't
    # happen often given outer gate, but keeps edge cases sane).
    if head_n + tail_n >= n:
        return _join_fit(lines, max_chars=MAX_SUMMARY_CHARS)

    omitted = n - head_n - tail_n
    head = lines[:head_n]
    tail = lines[-tail_n:] if tail_n else []
    marker = _omit_marker(omitted)
    parts = head + [marker] + tail
    return _join_fit(parts, max_chars=MAX_SUMMARY_CHARS)


def _summarize_failure(lines: list[str]) -> str:
    """Tail-first: pack as many trailing lines as fit in line + char budget."""
    n = len(lines)
    if n == 0:
        return ""

    # Prefer up to MAX_SUMMARY_LINES from the end; then fit chars without
    # chopping the newest (last) lines.
    take = min(MAX_SUMMARY_LINES, n)
    tail = lines[-take:]
    omitted = n - take

    if omitted <= 0:
        return _join_fit(tail, max_chars=MAX_SUMMARY_CHARS)

    marker = _omit_marker(omitted)
    # Optional tiny head context (1 line) if we still have room after tail+marker.
    parts = [marker] + tail
    body = _join_fit(parts, max_chars=MAX_SUMMARY_CHARS)
    # If budget allows and we omitted a lot, prepend first line for orientation.
    if omitted > 0 and lines:
        head_line = lines[0]
        trial = head_line + "\n" + body
        if len(trial) <= MAX_SUMMARY_CHARS and body.count("\n") + 1 < MAX_SUMMARY_LINES:
            # Only add head if marker is still present (still summarized).
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
            f"── {label}  page {p + 1}/{n_pages} ── "
            f"Enter:下一页  b:上一页  q:返回 ──"
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


class TerminalPresenter:
    """Thin terminal presentation layer for the CLI REPL.

    Consumes Runtime tool events and last-tool text. Does not call tools,
    adapters, or mutate Runtime / ToolResult.
    """

    def __init__(
        self,
        writer: Optional[Writer] = None,
        input_fn: Optional[InputFn] = None,
        page_size: Optional[int] = None,
    ):
        self._write = writer or print
        self._input = input_fn or input
        self._page_size = page_size  # None → dynamic

    def on_tool_start(self, event) -> None:
        data = getattr(event, "data", None) or {}
        name = data.get("name") or "?"
        self._write(f"\n🔧 [{name}] ...", end="", flush=True)

    def on_tool_end(self, event) -> None:
        data = getattr(event, "data", None) or {}
        ok = bool(data.get("success"))
        mark = "✅" if ok else "❌"
        self._write(f" {mark}", flush=True)
        disp = (data.get("display") or "").strip()
        if not disp:
            return
        body = summarize_tool_display(disp, success=ok)
        if body:
            self._write(body, flush=True)

    def show_assistant(self, text: str) -> None:
        if not text:
            return
        self._write(f"\n🤖 {text}")

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

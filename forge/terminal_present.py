"""Terminal presentation helpers (Batch 1: tool-output summary only).

This module intentionally stays thin. Pager / Presenter / heartbeat / streaming
belong in later batches — do not grow this file into a full TUI here.
"""
from __future__ import annotations

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

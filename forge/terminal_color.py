"""Forge terminal visual semantics — ANSI 24-bit foreground only.

Black background is owned by the host (e.g. Termux). Forge never sets
background. Colors express status / attention, not decoration.

Truecolor (no capability detection; target environment is Termux).
"""
from __future__ import annotations

# Named palette (defined even if UI does not use every entry yet).
PHOSPHOR = "\x1b[38;2;34;204;136m"       # #22CC88 — success / default phosphor
OSCILLOSCOPE = "\x1b[38;2;0;220;130m"    # #00DC82 — active / tool start
ALARM = "\x1b[38;2;255;85;0m"            # #FF5500 — failure
TUBE_BLUE = "\x1b[38;2;80;100;140m"      # #50648C — secondary / heartbeat / pager
AMBER = "\x1b[38;2;255;191;0m"           # #FFBF00 — assistant / warn
DEEP_BLUE = "\x1b[38;2;0;102;204m"       # #0066CC — reserved info
AQUA = "\x1b[38;2;127;255;212m"          # #7FFFD4 — reserved highlight

RESET = "\x1b[0m"


def paint(text: str, color: str) -> str:
    """Return color + text + RESET. Always terminates with RESET."""
    if text is None:
        text = ""
    return f"{color}{text}{RESET}"


def rgb(r: int, g: int, b: int) -> str:
    """Build a truecolor foreground sequence (for tests / extensions)."""
    return f"\x1b[38;2;{int(r)};{int(g)};{int(b)}m"

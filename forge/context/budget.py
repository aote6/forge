"""Content budget — load as much useful content as fits."""

from __future__ import annotations

MAX_FILE_SIZE = 65536       # 64KB per file
MAX_TOTAL_CONTENT = 524288  # 512KB total


def load_content(file_path: str, file_size: int, remaining: int) -> str | None:
    """Load file content if within budget. Returns None if skipped."""
    if file_size > MAX_FILE_SIZE:
        return None
    if file_size > remaining:
        return None
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except Exception:
        return None

"""共享 helper：操作日志与输出截断常量。

被 read_tools / search_tools / git_tools / test_tools / world_tools /
meta_tools 各子模块复用；local_tools.py 再导出以保持向后兼容。
"""

from __future__ import annotations

import json
import time
from pathlib import Path

LOG_PATH = Path.home() / "forge" / ".forge" / "operation_log.jsonl"
MAX_OUTPUT_CHARS = 8000


def _log(name: str, args: dict, success: bool, note: str = ""):
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": time.time(), "tool": name, "args": args,
        "success": success, "note": note[:200],
    }
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _truncate(text: str) -> str:
    """Default: keep the tail (errors usually at the end)."""
    if len(text) > MAX_OUTPUT_CHARS:
        return "...[输出已截断前部]\n\n" + text[-MAX_OUTPUT_CHARS:]
    return text


def _truncate_head(text: str) -> str:
    if len(text) > MAX_OUTPUT_CHARS:
        return text[:MAX_OUTPUT_CHARS] + "\n\n...[输出已截断]"
    return text

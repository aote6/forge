"""Session-level mutation inventory (what the agent changed this session)."""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


_LOG: list[dict[str, Any]] = []


def clear() -> None:
    _LOG.clear()


def record(
    path: str,
    *,
    tx_id: Any = None,
    tool: str = "",
    summary: str = "",
    project_root: str | None = None,
) -> None:
    entry = {
        "ts": time.time(),
        "path": path,
        "tx": tx_id,
        "tool": tool,
        "summary": (summary or "")[:200],
    }
    _LOG.append(entry)
    if project_root:
        try:
            _persist(project_root)
        except Exception:
            pass


def list_changes() -> list[dict[str, Any]]:
    return list(_LOG)


def format_list() -> str:
    if not _LOG:
        return "(本会话尚无文件修改)"
    lines = []
    for i, e in enumerate(_LOG, 1):
        lines.append(
            f"{i}. path={e.get('path')} tx={e.get('tx')} tool={e.get('tool')} "
            f"summary={e.get('summary')}"
        )
    return "\n".join(lines)


def _persist(project_root: str) -> None:
    d = Path(project_root) / ".forge"
    d.mkdir(parents=True, exist_ok=True)
    path = d / "session_changes.json"
    path.write_text(json.dumps(_LOG, ensure_ascii=False, indent=2), encoding="utf-8")


def load_into_memory(project_root: str) -> None:
    """Optional: load previous session file (does not auto-merge unless called)."""
    path = Path(project_root) / ".forge" / "session_changes.json"
    if not path.is_file():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            _LOG.clear()
            _LOG.extend(data[-50:])
    except Exception:
        pass

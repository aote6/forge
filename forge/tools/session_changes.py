"""Session-level mutation inventory (what the agent changed this session)."""
from __future__ import annotations

import json
import sys
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
    direct_disk: bool = False,
) -> None:
    entry = {
        "ts": time.time(),
        "path": path,
        "tx": tx_id,
        "tool": tool,
        "summary": (summary or "")[:200].replace("\n", " ").replace("\r", " "),
    }
    # P2-1c: direct_disk 写入做结构化标记，供启动/forge_sync 检测待对账文件。
    if direct_disk:
        entry["direct_disk"] = True
    _LOG.append(entry)
    if project_root:
        try:
            _persist(project_root, entry)
        except Exception as e:
            print(f"[session_changes] persist failed: {e}", file=sys.stderr)


def list_changes() -> list[dict[str, Any]]:
    return list(_LOG)


def pending_direct_disk(project_root: str) -> list[dict[str, Any]]:
    """返回持久化日志里标记 direct_disk 的条目（待对账文件）。

    P2-1c：direct_disk 写入不产生 World receipt，恢复 veritasd 后需要 forge_sync
    把这些磁盘变更 FAST_FORWARD 回 World。此处只读持久化文件（跨进程/重启可用），
    不依赖进程内 `_LOG`；只提示、不自动对账。
    """
    path = Path(project_root) / ".forge" / "session_changes.jsonl"
    if not path.is_file():
        return []
    out: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except Exception:
                continue
            if entry.get("direct_disk"):
                out.append(entry)
    except Exception as e:
        print(f"[session_changes] pending_direct_disk read failed: {e}", file=sys.stderr)
    return out


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


def _persist(project_root: str, entry: dict[str, Any] | None = None) -> None:
    """Append-only: 只写这一条,不再每次全量重写整个日志."""
    d = Path(project_root) / ".forge"
    d.mkdir(parents=True, exist_ok=True)
    path = d / "session_changes.jsonl"
    line = json.dumps(entry if entry is not None else (_LOG[-1] if _LOG else {}), ensure_ascii=False)
    with path.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_into_memory(project_root: str) -> None:
    """Optional: load previous session file (does not auto-merge unless called)."""
    path = Path(project_root) / ".forge" / "session_changes.jsonl"
    if not path.is_file():
        return
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        entries: list[dict[str, Any]] = []
        for ln in lines[-50:]:
            ln = ln.strip()
            if not ln:
                continue
            try:
                entries.append(json.loads(ln))
            except Exception:
                continue
        _LOG.clear()
        _LOG.extend(entries)
    except Exception as e:
        print(f"[session_changes] load failed: {e}", file=sys.stderr)

"""Shadow-file based last-tx undo (MVP when kernel has no time-travel).

语义（P3-5 文档化）：
`undo_last` 只把 shadow 记录的文件写前内容恢复到磁盘，**不回滚 World 账本**：
- 不写任何 World receipt（不伪造 external_sync、不回退 World version）；
- 不触碰 `.forge/sync_state.json` 的 `disk_synced_version`；
- 因此 undo 后 World 账本可能仍比磁盘更新，调用方必须让用户「以磁盘 read 为准」，
  待 veritasd 恢复后用 forge_sync 对账。

这是 MVP 语义：磁盘是 shadow 恢复的唯一权威；World 侧回滚属超范围（需内核
时间旅行或显式写 external_sync receipt，本模块都不做）。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


def _dir(project_root: str) -> Path:
    return Path(project_root) / ".forge" / "tx_shadow"


def record_tx(
    project_root: str,
    tx_id: Any,
    version: Any,
    files: dict[str, str],
) -> None:
    """files: path -> content BEFORE mutation."""
    d = _dir(project_root)
    d.mkdir(parents=True, exist_ok=True)
    stack_path = d / "stack.json"
    stack: list = []
    if stack_path.is_file():
        try:
            stack = json.loads(stack_path.read_text(encoding="utf-8"))
        except Exception:
            stack = []
    entry = {
        "tx_id": tx_id,
        "version": version,
        "ts": time.time(),
        "files": {},
    }
    for path, content in files.items():
        safe = path.replace("/", "__")
        fp = d / f"{tx_id}_{safe}.pre"
        fp.write_text(content if content is not None else "", encoding="utf-8")
        entry["files"][path] = str(fp.name)
    stack.append(entry)
    stack = stack[-5:]  # depth 5
    stack_path.write_text(json.dumps(stack, ensure_ascii=False, indent=2), encoding="utf-8")


def undo_last(project_root: str) -> dict[str, Any]:
    """从 shadow 恢复最近一次 mutation 的磁盘文件（纯磁盘操作）。

    注意（P3-5 文档化）：本函数不写 World receipt、不回滚 World 账本、不推进/回退
    disk_synced_version。undo 后 World 账本可能仍比磁盘更新——这由调用方
    （undo_last_tx）在 display 里向用户明示，最终以磁盘 read 为准，随后靠
    forge_sync 对账。
    """
    d = _dir(project_root)
    stack_path = d / "stack.json"
    if not stack_path.is_file():
        return {"ok": False, "error": "no tx shadow stack"}
    try:
        stack = json.loads(stack_path.read_text(encoding="utf-8"))
    except Exception as e:
        return {"ok": False, "error": str(e)}
    if not stack:
        return {"ok": False, "error": "stack empty"}
    entry = stack.pop()
    restored = []
    root = Path(project_root)
    for path, fname in (entry.get("files") or {}).items():
        fp = d / fname
        if not fp.is_file():
            continue
        content = fp.read_text(encoding="utf-8", errors="replace")
        target = root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        restored.append(path)
    stack_path.write_text(json.dumps(stack, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "ok": True,
        "undone_tx": entry.get("tx_id"),
        "restored_version": entry.get("version"),
        "paths": restored,
        "mode": "file_shadow_revert",
    }

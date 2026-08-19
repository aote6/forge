"""Shadow-file based last-tx undo (MVP when kernel has no time-travel)."""
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
    """Restore last shadowed files. Returns status dict."""
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

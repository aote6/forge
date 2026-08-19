"""Per-process file read cache keyed by path + mtime_ns + size."""
from __future__ import annotations

from pathlib import Path
from typing import Any


_CACHE: dict[str, dict[str, Any]] = {}


def _key(project_root: str, path: str) -> str:
    return f"{project_root}::{path.replace(chr(92), '/')}"


def get(project_root: str, path: str) -> tuple[str, dict] | None:
    """Return (text, meta) if cache hit and file unchanged."""
    root = Path(project_root)
    fp = root / path
    if not fp.is_file():
        return None
    try:
        st = fp.stat()
        sig = (st.st_mtime_ns, st.st_size)
    except OSError:
        return None
    k = _key(project_root, path)
    ent = _CACHE.get(k)
    if not ent or ent.get("sig") != sig:
        return None
    return ent["text"], {"from_cache": True, "path": path, "sig": sig}


def put(project_root: str, path: str, text: str) -> None:
    root = Path(project_root)
    fp = root / path
    try:
        st = fp.stat()
        sig = (st.st_mtime_ns, st.st_size)
    except OSError:
        return
    k = _key(project_root, path)
    _CACHE[k] = {"text": text, "sig": sig}
    # bound size
    if len(_CACHE) > 64:
        # drop arbitrary oldest half
        for drop in list(_CACHE.keys())[:32]:
            _CACHE.pop(drop, None)


def invalidate(project_root: str, path: str | None = None) -> None:
    if path is None:
        # clear all for this root
        prefix = f"{project_root}::"
        for k in list(_CACHE.keys()):
            if k.startswith(prefix):
                _CACHE.pop(k, None)
        return
    _CACHE.pop(_key(project_root, path), None)


def clear() -> None:
    _CACHE.clear()

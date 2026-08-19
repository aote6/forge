"""Classify tool failures for clearer recovery hints."""
from __future__ import annotations

import re


_VERITAS_PAT = re.compile(
    r"connection refused|connect\s+error|veritasd|broken pipe|"
    r"connection reset|errno\s*111|errno\s*61|not online|session closed|"
    r"failed to connect|no such file.*veritas|wal.*lock",
    re.I,
)


def classify_error(exc_or_msg: str | BaseException) -> dict:
    msg = str(exc_or_msg)
    if _VERITAS_PAT.search(msg):
        return {
            "kind": "veritasd_offline",
            "hint": (
                "veritasd 不在线或连接失败。"
                "文件级 shadow 编辑/undo 可能仍可用；"
                "World 操作（create_object/link）不可用。请启动 veritasd 后重试。"
            ),
        }
    return {"kind": "generic", "hint": ""}


def decorate_fail_message(base: str, exc_or_msg: str | BaseException | None = None) -> str:
    src = exc_or_msg if exc_or_msg is not None else base
    info = classify_error(src)
    if info["kind"] == "veritasd_offline":
        return f"{base}\nVERITAS: offline\nHINT: {info['hint']}"
    return base

"""Mobile-friendly, multi-AI copy/paste display blocks for tool results."""
from __future__ import annotations

from typing import Any, Mapping


def format_block(
    tool: str,
    status: str,
    kv: Mapping[str, Any] | None = None,
    body: str = "",
    hint: str = "",
    clip: Mapping[str, Any] | None = None,
) -> str:
    """Build a copy-paste friendly block.

    === FORGE/<tool> ===
    STATUS: OK|FAIL
    key: value
    --- BODY ---
    ...
    HINT: ...
    === FORGE/CLIP ===   (optional short relay block)
    --- END FORGE ---
    """
    st = (status or "OK").upper()
    if st in ("OK", "SUCCESS", "TRUE"):
        st = "OK"
    elif st in ("FAIL", "FAILED", "ERROR", "FALSE"):
        st = "FAIL"
    from forge.core.sanitizer import redact_secrets
    lines = [f"=== FORGE/{tool} ===", f"STATUS: {st}"]
    if kv:
        for k, v in kv.items():
            if v is None:
                continue
            val = str(v).replace("\n", " ").strip()
            if len(val) > 200:
                val = val[:200] + "…"
            lines.append(f"{k}: {val}")
    if body:
        lines.append("--- BODY ---")
        lines.append(body.rstrip())
    if hint:
        lines.append(f"HINT: {hint}")
    if clip:
        lines.append("=== FORGE/CLIP ===")
        for k, v in clip.items():
            if v is None:
                continue
            lines.append(f"{k}: {str(v).replace(chr(10), ' ').strip()[:180]}")
    lines.append("--- END FORGE ---")
    return redact_secrets("\n".join(lines))


def snippet_around(text: str, needle: str | None = None, max_lines: int = 6) -> str:
    """Return a short snippet; if needle found, center on first match."""
    if not text:
        return "(empty)"
    lines = text.splitlines()
    if needle and needle in text:
        # find line index of first match
        idx = 0
        pos = text.find(needle)
        idx = text[:pos].count("\n")
        lo = max(0, idx - max_lines // 2)
        hi = min(len(lines), lo + max_lines)
        chunk = lines[lo:hi]
        return "\n".join(chunk)
    return "\n".join(lines[:max_lines])


def error_slices(text: str, window: int = 20, max_slices: int = 3) -> str:
    """Extract windows around Traceback/Error/FAILED markers."""
    if not text:
        return ""
    import re

    keys = re.compile(
        r"(?i)(traceback \(most recent call last\)|^traceback\b|\berror\b|\bfailed\b|exception:|assertionerror)"
    )
    lines = text.splitlines()
    hits = []
    for i, line in enumerate(lines):
        if keys.search(line):
            hits.append(i)
    if not hits:
        return ""
    blocks = []
    used = set()
    for i in hits[: max_slices * 2]:
        lo = max(0, i - window)
        hi = min(len(lines), i + window + 1)
        key = (lo, hi)
        if key in used:
            continue
        # merge overlapping
        overlap = False
        for ulo, uhi in list(used):
            if not (hi < ulo or lo > uhi):
                overlap = True
                break
        if overlap:
            continue
        used.add(key)
        blocks.append("\n".join(lines[lo:hi]))
        if len(blocks) >= max_slices:
            break
    return "\n\n----\n\n".join(blocks)

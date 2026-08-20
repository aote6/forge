"""Minimal unified-diff application (no external deps)."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any


def parse_unified_diff(patch: str) -> list[dict[str, Any]]:
    """Parse unified diff into per-file hunks.

    Returns list of {path, hunks: [{old_start, old_count, lines: [...]}]}.
    lines use ' ','+','-' prefixes.
    """
    if not patch or not patch.strip():
        return []
    files: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    hunk: dict[str, Any] | None = None
    lines = patch.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("--- "):
            # start new file; path after --- a/ or ---
            raw = line[4:].strip()
            if raw.startswith("a/"):
                raw = raw[2:]
            # skip /dev/null
            path_old = raw.split("\t")[0]
            i += 1
            path_new = path_old
            if i < len(lines) and lines[i].startswith("+++ "):
                raw2 = lines[i][4:].strip()
                if raw2.startswith("b/"):
                    raw2 = raw2[2:]
                path_new = raw2.split("\t")[0]
                i += 1
            path = path_new if path_new != "/dev/null" else path_old
            if path == "/dev/null":
                current = None
                continue
            current = {"path": path, "hunks": []}
            files.append(current)
            hunk = None
            continue
        if line.startswith("@@") and current is not None:
            m = re.match(r"@@\s+-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s+@@", line)
            if not m:
                i += 1
                continue
            old_start = int(m.group(1))
            old_count = int(m.group(2) or "1")
            hunk = {
                "old_start": old_start,
                "old_count": old_count,
                "lines": [],
            }
            current["hunks"].append(hunk)
            i += 1
            continue
        if hunk is not None and (line.startswith(" ") or line.startswith("+") or line.startswith("-") or line == "\\ No newline at end of file"):
            if line.startswith("\\"):
                i += 1
                continue
            hunk["lines"].append(line)
            i += 1
            continue
        i += 1
    return files


def _apply_hunks_to_text(original: str, hunks: list[dict]) -> str:
    """Apply hunks to original text; raise ValueError on mismatch."""
    # Work on list of lines without newlines for matching
    plain = original.splitlines()
    # Track if original ended with newline
    ends_nl = original.endswith("\n") if original else True

    for hunk in hunks:
        start = hunk["old_start"] - 1  # 0-based
        body = hunk["lines"]
        old_chunk = []
        new_chunk = []
        for bl in body:
            if bl.startswith(" "):
                old_chunk.append(bl[1:])
                new_chunk.append(bl[1:])
            elif bl.startswith("-"):
                old_chunk.append(bl[1:])
            elif bl.startswith("+"):
                new_chunk.append(bl[1:])

        # Verify old_chunk matches at start
        end = start + len(old_chunk)
        actual = plain[start:end]
        if actual != old_chunk:
            # try relaxed: strip trailing spaces
            if [a.rstrip() for a in actual] != [b.rstrip() for b in old_chunk]:
                raise ValueError(
                    f"hunk mismatch at line {start + 1}: expected {old_chunk[:3]!r}... "
                    f"got {actual[:3]!r}..."
                )
        plain = plain[:start] + new_chunk + plain[end:]

    text = "\n".join(plain)
    if ends_nl and (text and not text.endswith("\n")):
        text += "\n"
    if not plain and ends_nl:
        text = ""
    return text


def apply_unified_patch_to_files(project_root: str, patch: str) -> dict:
    """Build plan of {path, new_content} after applying patch to disk files.

    Returns {"files": [{"path", "new_content"}], "error": optional}.
    """
    try:
        parsed = parse_unified_diff(patch)
    except Exception as e:
        return {"files": [], "error": f"parse error: {e}"}
    if not parsed:
        return {"files": [], "error": "empty or unrecognized diff"}

    root = Path(project_root)
    out = []
    for f in parsed:
        path = f["path"]
        fp = root / path
        if fp.is_file():
            original = fp.read_text(encoding="utf-8", errors="replace")
        else:
            original = ""
        try:
            new_content = _apply_hunks_to_text(original, f["hunks"])
        except ValueError as e:
            return {"files": [], "error": f"{path}: {e}"}
        out.append({"path": path, "old_content": original, "new_content": new_content})
    return {"files": out}

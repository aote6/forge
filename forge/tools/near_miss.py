"""Find near-miss snippets when str_replace old_string not found."""
from __future__ import annotations

import re


def _score(a: str, b: str) -> float:
    """Simple similarity: ratio of shared lines / chars."""
    if not a or not b:
        return 0.0
    a_s, b_s = a.strip(), b.strip()
    if a_s == b_s:
        return 1.0
    if a_s in b_s or b_s in a_s:
        return 0.85
    # line overlap
    al = set(a_s.splitlines())
    bl = set(b_s.splitlines())
    if not al or not bl:
        return 0.0
    inter = len(al & bl)
    return inter / max(len(al), len(bl))


def find_near_misses(file_text: str, old_string: str, max_n: int = 3) -> list[str]:
    if not file_text or not old_string:
        return []
    needle = old_string.strip()
    lines = file_text.splitlines()
    window = max(3, min(12, needle.count("\n") + 3))
    candidates = []
    for i in range(0, max(1, len(lines) - window + 1)):
        chunk = "\n".join(lines[i : i + window])
        sc = _score(needle, chunk)
        if sc >= 0.3:
            candidates.append((sc, i + 1, chunk))
    # also try single-line best match
    first_line = needle.splitlines()[0].strip() if needle else ""
    if first_line:
        for i, ln in enumerate(lines):
            if first_line[:40] in ln or ln.strip()[:40] in first_line:
                lo = max(0, i - 2)
                hi = min(len(lines), i + 3)
                chunk = "\n".join(lines[lo:hi])
                candidates.append((0.5, lo + 1, chunk))
    candidates.sort(key=lambda x: -x[0])
    out = []
    seen = set()
    for sc, line_no, chunk in candidates:
        key = chunk.strip()[:120]
        if key in seen:
            continue
        seen.add(key)
        out.append(f"(~L{line_no}, score={sc:.2f})\n{chunk}")
        if len(out) >= max_n:
            break
    return out


def find_occurrence_lines(file_text: str, needle: str) -> list[int]:
    """Return 1-based line numbers where needle starts (exact substring)."""
    if not file_text or not needle:
        return []
    lines_out: list[int] = []
    start = 0
    while True:
        idx = file_text.find(needle, start)
        if idx < 0:
            break
        line_no = file_text.count("\n", 0, idx) + 1
        lines_out.append(line_no)
        start = idx + max(1, len(needle))
    return lines_out


def _normalize_ws(s: str) -> str:
    """Collapse all whitespace runs to single space for comparison."""
    return re.sub(r"\s+", " ", (s or "").strip())


def _normalize_quotes(s: str) -> str:
    return (s or "").replace("'", '"')


def diagnose_mismatch(file_text: str, old_string: str) -> dict | None:
    """Detect typical near-miss difference kinds (indent / whitespace / quotes).

    Returns dict with keys: kinds (list[str]), hint (str), optional match_line.
    """
    if not file_text or not old_string:
        return None
    needle = old_string
    lines = file_text.splitlines()
    n_lines = needle.splitlines()
    window = max(1, len(n_lines))

    best = None  # (score, line_no, chunk)
    for i in range(0, max(1, len(lines) - window + 1)):
        chunk = "\n".join(lines[i : i + window])
        sc = _score(needle, chunk)
        if best is None or sc > best[0]:
            best = (sc, i + 1, chunk)

    if best is None or best[0] < 0.25:
        fl = (n_lines[0].strip() if n_lines else "")[:60]
        if fl:
            for i, ln in enumerate(lines):
                if fl in ln or ln.strip()[:40] in fl:
                    lo = max(0, i)
                    hi = min(len(lines), i + window)
                    chunk = "\n".join(lines[lo:hi])
                    best = (0.4, lo + 1, chunk)
                    break

    # Quote-normalized exact line match (score was 0 because quotes differ)
    if best is None or best[0] < 0.5:
        nq = _normalize_quotes(needle.strip())
        for i, ln in enumerate(lines):
            if _normalize_quotes(ln.strip()) == nq:
                chunk = ln
                # multi-line needle: take window from here
                if window > 1:
                    chunk = "\n".join(lines[i : i + window])
                if best is None or 0.9 > best[0]:
                    best = (0.9, i + 1, chunk)
                break

    if best is None:
        return None

    _, line_no, chunk = best
    kinds: list[str] = []
    hints: list[str] = []

    if needle.strip() == chunk.strip() and needle != chunk:
        kinds.append("whitespace")
        hints.append("仅首尾空白不同：去掉 old_string 首尾空白后可匹配")

    def strip_indent(s: str) -> str:
        return "\n".join(ln.lstrip() for ln in s.splitlines())

    if strip_indent(needle) == strip_indent(chunk) and needle != chunk:
        if "indent" not in kinds:
            kinds.append("indent")
        hints.append("仅缩进不同：请复制文件中的原始缩进作为 old_string")

    if _normalize_ws(needle) == _normalize_ws(chunk) and needle != chunk:
        if "whitespace" not in kinds:
            kinds.append("whitespace")
        if not any("空白" in h for h in hints):
            hints.append("空白/换行布局不同：使用下方建议片段原样复制")

    if _normalize_quotes(needle.strip()) == _normalize_quotes(chunk.strip()) and needle.strip() != chunk.strip():
        kinds.append("quotes")
        hints.append("仅引号风格不同（' vs \"）：请使用文件中的原始引号")

    if not kinds and best[0] >= 0.5:
        kinds.append("similar")
        hints.append("内容相近但不完全相同：请用建议片段作为 old_string")

    if not kinds:
        return None

    return {
        "kinds": kinds,
        "hint": "；".join(hints),
        "match_line": line_no,
        "match_text": chunk,
    }


def suggest_old_string(file_text: str, old_string: str) -> dict | None:
    """If a unique fuzzy match exists, return copyable suggested old_string.

    Returns {"line": int, "text": str} where text is the exact file substring
    (best-effort window) that the model should use as old_string.
    """
    if not file_text or not old_string:
        return None
    needle = old_string.strip()
    if not needle:
        return None
    lines = file_text.splitlines()
    n_lines = needle.splitlines()
    window = max(1, len(n_lines))

    candidates: list[tuple[float, int, str]] = []
    for i in range(0, max(1, len(lines) - window + 1)):
        chunk = "\n".join(lines[i : i + window])
        sc = _score(needle, chunk)
        if sc >= 0.45:
            candidates.append((sc, i + 1, chunk))

    exactish = [
        c
        for c in candidates
        if c[2].strip() == needle
        or "\n".join(ln.lstrip() for ln in c[2].splitlines())
        == "\n".join(ln.lstrip() for ln in needle.splitlines())
    ]
    pool = exactish if exactish else candidates
    if not pool:
        return None
    pool.sort(key=lambda x: -x[0])
    if len(pool) == 1 or (len(pool) >= 2 and pool[0][0] - pool[1][0] >= 0.15):
        sc, line_no, chunk = pool[0]
        return {"line": line_no, "text": chunk, "score": sc}
    return None

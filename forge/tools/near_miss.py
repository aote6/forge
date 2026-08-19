"""Find near-miss snippets when str_replace old_string not found."""
from __future__ import annotations


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

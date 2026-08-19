"""Find tests likely related to a source file (lightweight, no LSP)."""
from __future__ import annotations

import re
from pathlib import Path


def _module_stem(path: str) -> str:
    p = path.replace("\\", "/").lstrip("./")
    name = Path(p).stem
    if name.startswith("test_"):
        name = name[5:]
    elif name.endswith("_test"):
        name = name[:-5]
    return name


def find_related_tests(project_root: str, path: str, max_n: int = 8) -> list[str]:
    """Return relative test file paths that likely cover `path`."""
    root = Path(project_root)
    rel = path.replace("\\", "/").lstrip("./")
    stem = _module_stem(rel)
    if not stem:
        return []

    candidates: list[str] = []
    # Convention: tests/test_<stem>.py, test/<stem>_test.py
    patterns = [
        f"**/test_{stem}.py",
        f"**/{stem}_test.py",
        f"**/tests/test_{stem}.py",
    ]
    seen = set()
    for pat in patterns:
        for m in root.glob(pat):
            if not m.is_file():
                continue
            try:
                r = str(m.relative_to(root)).replace("\\", "/")
            except ValueError:
                continue
            if r not in seen:
                seen.add(r)
                candidates.append(r)

    # Content search: files under tests/ that import or mention the module
    tests_dirs = [root / "tests", root / "test"]
    token = stem
    import_re = re.compile(
        rf"(?:from\s+[\w.]+\s+import\s+.*\b{re.escape(token)}\b|"
        rf"import\s+[\w.]*\b{re.escape(token)}\b|"
        rf"{re.escape(Path(rel).name)})"
    )
    for td in tests_dirs:
        if not td.is_dir():
            continue
        for f in td.rglob("test_*.py"):
            try:
                r = str(f.relative_to(root)).replace("\\", "/")
            except ValueError:
                continue
            if r in seen:
                continue
            try:
                text = f.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if token in text or Path(rel).name in text or import_re.search(text):
                seen.add(r)
                candidates.append(r)
            if len(candidates) >= max_n:
                return candidates[:max_n]
    return candidates[:max_n]


def format_related_hint(project_root: str, path: str) -> str:
    tests = find_related_tests(project_root, path)
    if not tests:
        return "RELATED_TESTS: (none found) → run_test_structured() 或全量"
    joined = ", ".join(tests)
    # suggest a focused pytest target
    target = tests[0]
    return (
        f"RELATED_TESTS ({len(tests)}): {joined}\n"
        f"HINT: run_test_structured(target={target!r}) 优先于全量"
    )

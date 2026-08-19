"""Find tests likely related to a source file + honest coverage hints."""
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
    patterns = [
        f"**/test_{stem}.py",
        f"**/{stem}_test.py",
        f"**/tests/test_{stem}.py",
    ]
    seen: set[str] = set()
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


def coverage_hint(project_root: str, path: str, symbol_hint: str | None = None) -> str:
    """Static mention check — NOT real line coverage."""
    tests = find_related_tests(project_root, path)
    if not tests:
        return (
            "COVERAGE_HINT: 未找到相关测试文件；"
            "测试全绿也不能证明本次改动被验证。建议补测或人工验收。"
        )
    root = Path(project_root)
    stem = _module_stem(path)
    sym = symbol_hint or stem
    lines: list[str] = []
    strong = 0
    for rel in tests:
        fp = root / rel
        try:
            text = fp.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        mentions_sym = bool(sym) and sym in text
        mentions_file = Path(path).name in text or stem in text
        if mentions_sym and ("assert" in text or "raises" in text):
            lines.append(f"  - {rel}: 提到 {sym} 且含 assert → 可能相关")
            strong += 1
        elif mentions_file or mentions_sym:
            lines.append(f"  - {rel}: 仅引用模块/符号 → 弱相关")
        else:
            lines.append(f"  - {rel}: 约定匹配但正文未直接提到 → 覆盖不明")
    if strong == 0:
        lines.append("  结论: 未发现对改动点的直接断言；绿测 ≠ 行为已验证。")
    else:
        lines.append(f"  结论: {strong} 个测试可能打到相关符号；仍非行级覆盖率。")
    return "COVERAGE_HINT:\n" + "\n".join(lines)


def format_related_hint(project_root: str, path: str, symbol_hint: str | None = None) -> str:
    tests = find_related_tests(project_root, path)
    if not tests:
        base = "RELATED_TESTS: (none found) → run_test_structured() 或全量"
    else:
        joined = ", ".join(tests)
        target = tests[0]
        base = (
            f"RELATED_TESTS ({len(tests)}): {joined}\n"
            f"HINT: run_test_structured(target={target!r}) 优先于全量"
        )
    return base + "\n" + coverage_hint(project_root, path, symbol_hint)

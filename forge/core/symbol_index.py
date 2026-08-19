"""Lightweight whole-repo symbol index (no LSP).

Builds a JSON cache at <project>/.forge/symbols.json:
  { "symbols": { name: [ {kind, path, start_line, end_line, qualname}, ... ] },
    "files": { rel_path: mtime },
    "version": 1 }

find_symbol_definition / read_function query this index instead of full AST walks.
"""
from __future__ import annotations

import ast
import json
import os
import time
from pathlib import Path
from typing import Any

INDEX_VERSION = 1
INDEX_REL = ".forge/symbols.json"
SKIP_DIRS = {
    ".git", ".forge", "__pycache__", ".venv", "venv", "node_modules",
    ".mypy_cache", ".pytest_cache", "dist", "build", ".tox",
}


def _index_path(project_root: str | Path) -> Path:
    return Path(project_root).expanduser().resolve() / INDEX_REL


def _iter_py_files(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for name in filenames:
            if name.endswith(".py"):
                yield Path(dirpath) / name


def _extract_symbols(path: Path, root: Path) -> list[dict[str, Any]]:
    try:
        src = path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(src, filename=str(path))
    except (SyntaxError, OSError, ValueError):
        return []
    rel = str(path.relative_to(root)).replace("\\", "/")
    out: list[dict[str, Any]] = []

    def walk(node: ast.AST, prefix: str = ""):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                kind = "class" if isinstance(child, ast.ClassDef) else "function"
                qual = f"{prefix}.{child.name}" if prefix else child.name
                out.append({
                    "name": child.name,
                    "qualname": qual,
                    "kind": kind,
                    "path": rel,
                    "start_line": child.lineno,
                    "end_line": getattr(child, "end_lineno", child.lineno) or child.lineno,
                })
                walk(child, qual)
            elif isinstance(child, (ast.Module, ast.If, ast.For, ast.While, ast.With, ast.Try)):
                walk(child, prefix)

    walk(tree)
    # module-level assignments (simple names)
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name):
                    out.append({
                        "name": t.id,
                        "qualname": t.id,
                        "kind": "variable",
                        "path": rel,
                        "start_line": node.lineno,
                        "end_line": getattr(node, "end_lineno", node.lineno) or node.lineno,
                    })
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            out.append({
                "name": node.target.id,
                "qualname": node.target.id,
                "kind": "variable",
                "path": rel,
                "start_line": node.lineno,
                "end_line": getattr(node, "end_lineno", node.lineno) or node.lineno,
            })
    return out


def build_symbol_index(project_root: str | Path, *, force: bool = False) -> dict[str, Any]:
    root = Path(project_root).expanduser().resolve()
    idx_path = _index_path(root)
    symbols: dict[str, list[dict]] = {}
    files: dict[str, float] = {}
    for py in _iter_py_files(root):
        rel = str(py.relative_to(root)).replace("\\", "/")
        try:
            mtime = py.stat().st_mtime
        except OSError:
            continue
        files[rel] = mtime
        for sym in _extract_symbols(py, root):
            symbols.setdefault(sym["name"], []).append({
                k: sym[k] for k in ("kind", "path", "start_line", "end_line", "qualname")
            })
    data = {
        "version": INDEX_VERSION,
        "built_at": time.time(),
        "root": str(root),
        "files": files,
        "symbols": symbols,
    }
    idx_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = idx_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, idx_path)
    return data


def load_symbol_index(project_root: str | Path, *, rebuild_if_stale: bool = True) -> dict[str, Any]:
    root = Path(project_root).expanduser().resolve()
    idx_path = _index_path(root)
    if not idx_path.is_file():
        return build_symbol_index(root)
    try:
        data = json.loads(idx_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return build_symbol_index(root)
    if data.get("version") != INDEX_VERSION:
        return build_symbol_index(root)
    if not rebuild_if_stale:
        return data
    # cheap staleness: any tracked file mtime changed or new py file appeared
    cached_files = data.get("files") or {}
    current: dict[str, float] = {}
    for py in _iter_py_files(root):
        rel = str(py.relative_to(root)).replace("\\", "/")
        try:
            current[rel] = py.stat().st_mtime
        except OSError:
            continue
    if current != {k: float(v) for k, v in cached_files.items()}:
        return build_symbol_index(root)
    return data


def lookup_symbol(project_root: str | Path, symbol_name: str, *, exact: bool = True) -> list[dict]:
    data = load_symbol_index(project_root)
    symbols = data.get("symbols") or {}
    if exact:
        return list(symbols.get(symbol_name) or [])
    name_l = symbol_name.lower()
    hits = []
    for name, entries in symbols.items():
        if name_l in name.lower():
            hits.extend(entries)
    return hits


def lookup_function_range(project_root: str | Path, path: str, symbol_name: str) -> tuple[int, int] | None:
    """Return (start_line, end_line) for symbol in path, or None."""
    path_n = path.replace("\\", "/").lstrip("./")
    for e in lookup_symbol(project_root, symbol_name, exact=True):
        if e.get("path") == path_n or e.get("path", "").endswith("/" + path_n):
            return int(e["start_line"]), int(e["end_line"])
    # fallback: parse single file
    root = Path(project_root).expanduser().resolve()
    target = root / path
    if not target.is_file():
        return None
    try:
        tree = ast.parse(target.read_text(encoding="utf-8", errors="replace"))
    except (SyntaxError, OSError):
        return None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name == symbol_name:
                return node.lineno, getattr(node, "end_lineno", node.lineno) or node.lineno
    return None

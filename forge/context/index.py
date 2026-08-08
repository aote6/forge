"""RepositoryIndex — snapshot-bound Python symbol / reference model.

Python-first, stdlib ast only. No tree-sitter. No type inference.
Unresolved relations stay unresolved — never fabricated.
"""
from __future__ import annotations

import ast
import os
from dataclasses import dataclass, field
from typing import Iterable, Optional

from forge.context.snapshot import RepositorySnapshot, take_snapshot
from forge.context.scanner import scan_files, CODE_EXTENSIONS


@dataclass(frozen=True)
class Symbol:
    name: str
    qualified_name: str
    kind: str  # "function" | "async_function" | "class" | "method"
    file_path: str
    start_line: int
    end_line: int


@dataclass(frozen=True)
class Reference:
    symbol_name: str
    file_path: str
    line: int
    kind: str  # "name" | "attribute" | "call"
    resolved_qualified: Optional[str] = None  # None => unresolved


@dataclass(frozen=True)
class ImportRecord:
    file_path: str
    line: int
    module: str  # e.g. "forge.foo" or relative ".foo"
    names: tuple  # imported names; ("*",) for star; () for import module only
    is_from: bool


@dataclass
class RepositoryIndex:
    snapshot_id: str
    symbols: list[Symbol] = field(default_factory=list)
    references: list[Reference] = field(default_factory=list)
    imports: list[ImportRecord] = field(default_factory=list)
    files_indexed: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    # ── queries ──────────────────────────────────────────────

    def find_definition(self, symbol: str) -> list[Symbol]:
        """Exact name or qualified_name match."""
        out = []
        for s in self.symbols:
            if s.name == symbol or s.qualified_name == symbol:
                out.append(s)
            elif s.qualified_name.endswith("." + symbol):
                out.append(s)
        return out

    def find_symbol(self, symbol: str) -> list[Symbol]:
        return self.find_definition(symbol)

    def find_references(self, symbol: str) -> list[Reference]:
        """References to short name or matching resolved_qualified."""
        out = []
        for r in self.references:
            if r.symbol_name == symbol:
                out.append(r)
            elif r.resolved_qualified and (
                r.resolved_qualified == symbol
                or r.resolved_qualified.endswith("." + symbol)
            ):
                out.append(r)
        return out

    def affected_files(self, symbol: str) -> list[str]:
        files: set[str] = set()
        for s in self.find_definition(symbol):
            files.add(s.file_path)
        for r in self.find_references(symbol):
            files.add(r.file_path)
        return sorted(files)

    def summary_for_planner(self, focus_symbols: Optional[list[str]] = None) -> str:
        """Compact structured text for Planner prompt — not str(index)."""
        lines: list[str] = [
            f"RepositoryIndex snapshot_id={self.snapshot_id[:16]}...",
            f"files_indexed={len(self.files_indexed)} symbols={len(self.symbols)} "
            f"refs={len(self.references)} imports={len(self.imports)}",
        ]
        focus = focus_symbols or []
        if not focus:
            # sample top-level class/function names (stable order)
            names = sorted({s.name for s in self.symbols if s.kind in ("class", "function", "async_function")})
            focus = names[:40]
        for name in focus:
            defs = self.find_definition(name)
            refs = self.find_references(name)
            if not defs and not refs:
                continue
            lines.append(f"symbol {name}:")
            for d in defs[:8]:
                lines.append(
                    f"  def {d.kind} {d.qualified_name} @ {d.file_path}:{d.start_line}-{d.end_line}"
                )
            for r in refs[:20]:
                res = r.resolved_qualified or "UNRESOLVED"
                lines.append(f"  ref {r.kind} @{r.file_path}:{r.line} resolved={res}")
            aff = self.affected_files(name)
            if aff:
                lines.append(f"  affected_files: {', '.join(aff)}")
        return "\n".join(lines)

    def to_summary_dict(self) -> dict:
        return {
            "snapshot_id": self.snapshot_id,
            "file_count": len(self.files_indexed),
            "symbol_count": len(self.symbols),
            "reference_count": len(self.references),
            "import_count": len(self.imports),
            "error_count": len(self.errors),
        }

    # ── build ────────────────────────────────────────────────

    @classmethod
    def build(
        cls,
        repo_path: str,
        snapshot: Optional[RepositorySnapshot] = None,
    ) -> "RepositoryIndex":
        snap = snapshot or take_snapshot(repo_path)
        idx = cls(snapshot_id=snap.snapshot_id)
        files, scan_errors = scan_files(repo_path)
        for e in scan_errors:
            idx.errors.append(f"{e.path}:{e.reason}")

        py_files = [f for f in files if f.path.endswith(".py")]
        # deterministic order
        py_files = sorted(py_files, key=lambda f: f.path)

        for fe in py_files:
            full = os.path.join(repo_path, fe.path)
            try:
                with open(full, "r", encoding="utf-8", errors="replace") as fh:
                    source = fh.read()
            except OSError as e:
                idx.errors.append(f"{fe.path}:read:{e}")
                continue
            try:
                tree = ast.parse(source, filename=fe.path)
            except SyntaxError as e:
                idx.errors.append(f"{fe.path}:syntax:{e}")
                continue
            idx.files_indexed.append(fe.path)
            _index_module(tree, fe.path, idx)

        # stable ordering for determinism
        idx.symbols.sort(key=lambda s: (s.file_path, s.start_line, s.qualified_name))
        idx.references.sort(key=lambda r: (r.file_path, r.line, r.symbol_name, r.kind))
        idx.imports.sort(key=lambda i: (i.file_path, i.line, i.module))
        return idx


def _index_module(tree: ast.AST, file_path: str, idx: RepositoryIndex) -> None:
    # imports
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                idx.imports.append(
                    ImportRecord(
                        file_path=file_path,
                        line=getattr(node, "lineno", 0) or 0,
                        module=alias.name,
                        names=(),
                        is_from=False,
                    )
                )
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if node.level:
                mod = "." * node.level + mod
            names = tuple(a.name for a in node.names)
            idx.imports.append(
                ImportRecord(
                    file_path=file_path,
                    line=getattr(node, "lineno", 0) or 0,
                    module=mod,
                    names=names,
                    is_from=True,
                )
            )

    # definitions + scoped references via NodeVisitor
    visitor = _ModuleVisitor(file_path, idx)
    visitor.visit(tree)


class _ModuleVisitor(ast.NodeVisitor):
    def __init__(self, file_path: str, idx: RepositoryIndex):
        self.file_path = file_path
        self.idx = idx
        self.class_stack: list[str] = []
        # names defined in module/class scopes we treat as local defs
        self.defined_names: set[str] = set()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        q = ".".join(self.class_stack + [node.name]) if self.class_stack else node.name
        self.idx.symbols.append(
            Symbol(
                name=node.name,
                qualified_name=q,
                kind="class",
                file_path=self.file_path,
                start_line=node.lineno,
                end_line=getattr(node, "end_lineno", node.lineno) or node.lineno,
            )
        )
        self.defined_names.add(node.name)
        self.class_stack.append(node.name)
        self.generic_visit(node)
        self.class_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node, "method" if self.class_stack else "function")

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        kind = "method" if self.class_stack else "async_function"
        if not self.class_stack:
            kind = "async_function"
        else:
            kind = "method"
        self._visit_function(node, kind)

    def _visit_function(self, node, kind: str) -> None:
        if self.class_stack:
            q = ".".join(self.class_stack + [node.name])
        else:
            q = node.name
        self.idx.symbols.append(
            Symbol(
                name=node.name,
                qualified_name=q,
                kind=kind,
                file_path=self.file_path,
                start_line=node.lineno,
                end_line=getattr(node, "end_lineno", node.lineno) or node.lineno,
            )
        )
        self.defined_names.add(node.name)
        # visit body for references; do not treat nested defs specially beyond walk
        self.generic_visit(node)

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, (ast.Load, ast.Del)):
            self.idx.references.append(
                Reference(
                    symbol_name=node.id,
                    file_path=self.file_path,
                    line=node.lineno,
                    kind="name",
                    resolved_qualified=None,  # left unresolved without full binding analysis
                )
            )
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        # record attribute identifier as reference candidate (e.g. Foo.bar → bar, and value walk)
        if isinstance(node.ctx, ast.Load):
            self.idx.references.append(
                Reference(
                    symbol_name=node.attr,
                    file_path=self.file_path,
                    line=node.lineno,
                    kind="attribute",
                    resolved_qualified=None,
                )
            )
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        # strings must NOT become symbol references
        return

    def visit_JoinedStr(self, node: ast.JoinedStr) -> None:
        # f-string expression parts only
        for v in node.values:
            if not isinstance(v, ast.Constant):
                self.visit(v)


def extract_focus_symbols(task: str) -> list[str]:
    """Heuristic identifiers from task text for planner focus (not a full NER)."""
    import re
    # CapWords and simple identifiers after keywords
    found = re.findall(r"\b([A-Z][a-zA-Z0-9_]{1,}|[a-z_][a-zA-Z0-9_]{2,})\b", task or "")
    stop = {
        "the", "and", "for", "with", "from", "this", "that", "modify", "create",
        "delete", "update", "rename", "fix", "refactor", "class", "function",
        "method", "file", "files", "module", "please", "into", "all", "callers",
        # Common field / schema / instruction words — not symbols to refactor
        "name", "description", "parameters", "content", "type", "required",
        "properties", "object", "string", "integer", "array", "boolean",
        "return", "returns", "value", "values", "path", "default", "optional",
        "tools", "schemas", "forge", "loop", "tool", "list", "only", "must",
        "exact", "field", "fields", "keep", "remove", "replace",
    }
    out = []
    seen = set()
    for n in found:
        if n.lower() in stop or n in seen:
            continue
        seen.add(n)
        out.append(n)
    return out[:30]

"""Workspace metadata manifest for Layer B state observation.

Observes project_root engineering tree (metadata-only). Does not hash file
contents. Does not follow directory symlinks or paths outside the root.
"""
from __future__ import annotations

import fnmatch
import os
from pathlib import Path
from typing import Mapping, NamedTuple

# Directory names pruned during walk (exact name match on any level).
WORKSPACE_MANIFEST_EXCLUDE_DIR_NAMES: frozenset[str] = frozenset(
    {
        ".git",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".pyright",
        ".ruff_cache",
        ".venv",
        "venv",
        "node_modules",
        ".tox",
        ".nox",
        "dist",
        "build",
        ".eggs",
        ".idea",
        ".vscode",
        ".forge",  # v1: prune entire .forge tree
    }
)


class ManifestEntry(NamedTuple):
    kind: str  # file | dir | symlink | other
    mtime_ns: int | None
    size: int
    target: str | None  # symlink text; else None


def _entry_key(entry: ManifestEntry | str | object) -> object:
    """Comparable form for before/after equality (supports legacy str tests)."""
    if isinstance(entry, ManifestEntry):
        return (entry.kind, entry.mtime_ns, entry.size, entry.target)
    return entry


def build_workspace_manifest(
    project_root: str | os.PathLike,
) -> dict[str, ManifestEntry]:
    """Scan project_root into a relative-path → metadata map.

    - Metadata only (no content hash).
    - Does not follow directory symlinks.
    - Does not recurse outside project_root.
    - Prunes WORKSPACE_MANIFEST_EXCLUDE_DIR_NAMES.
    """
    root = Path(project_root).resolve()
    out: dict[str, ManifestEntry] = {}
    if not root.is_dir():
        return out

    def _add(rel: str, entry: ManifestEntry) -> None:
        out[rel] = entry

    stack: list[Path] = [root]
    while stack:
        current = stack.pop()
        try:
            with os.scandir(current) as it:
                for dent in it:
                    name = dent.name
                    if name in WORKSPACE_MANIFEST_EXCLUDE_DIR_NAMES:
                        # Prune: do not record children; skip the dir node itself
                        # so excluded trees never appear in the manifest.
                        continue
                    # Relative path must NOT resolve through symlinks — otherwise
                    # links pointing outside the root disappear from the manifest.
                    try:
                        rel_s = Path(dent.path).relative_to(root).as_posix()
                    except ValueError:
                        continue
                    try:
                        is_sym = dent.is_symlink()
                        if is_sym:
                            try:
                                target = os.readlink(dent.path)
                            except OSError:
                                target = None
                            try:
                                st = dent.stat(follow_symlinks=False)
                                mtime_ns = getattr(st, "st_mtime_ns", None)
                            except OSError:
                                mtime_ns = None
                            _add(
                                rel_s,
                                ManifestEntry(
                                    kind="symlink",
                                    mtime_ns=mtime_ns,
                                    size=0,
                                    target=str(target) if target is not None else None,
                                ),
                            )
                            # Never follow symlink into a directory tree.
                            continue
                        if dent.is_dir(follow_symlinks=False):
                            try:
                                st = dent.stat(follow_symlinks=False)
                                mtime_ns = getattr(st, "st_mtime_ns", None)
                            except OSError:
                                mtime_ns = None
                            _add(
                                rel_s,
                                ManifestEntry(
                                    kind="dir",
                                    mtime_ns=mtime_ns,
                                    size=0,
                                    target=None,
                                ),
                            )
                            stack.append(Path(dent.path))
                        elif dent.is_file(follow_symlinks=False):
                            try:
                                st = dent.stat(follow_symlinks=False)
                                _add(
                                    rel_s,
                                    ManifestEntry(
                                        kind="file",
                                        mtime_ns=getattr(st, "st_mtime_ns", None),
                                        size=int(st.st_size),
                                        target=None,
                                    ),
                                )
                            except OSError:
                                _add(
                                    rel_s,
                                    ManifestEntry(
                                        kind="other",
                                        mtime_ns=None,
                                        size=0,
                                        target=None,
                                    ),
                                )
                        else:
                            _add(
                                rel_s,
                                ManifestEntry(
                                    kind="other",
                                    mtime_ns=None,
                                    size=0,
                                    target=None,
                                ),
                            )
                    except OSError:
                        _add(
                            rel_s,
                            ManifestEntry(
                                kind="other",
                                mtime_ns=None,
                                size=0,
                                target=None,
                            ),
                        )
        except OSError:
            continue
    return out


def manifest_changed_paths(
    before: Mapping[str, object],
    after: Mapping[str, object],
) -> list[str]:
    """Symmetric path-level diff: added, removed, and modified keys."""
    keys = set(before.keys()) | set(after.keys())
    changed: list[str] = []
    for p in keys:
        if _entry_key(before.get(p)) != _entry_key(after.get(p)):  # type: ignore[arg-type]
            changed.append(p)
    changed.sort()
    return changed


def unauthorized_changed_paths(
    before: Mapping[str, object],
    after: Mapping[str, object],
    authorized_patterns: frozenset[str] | set[str],
) -> list[str]:
    """changed paths not matched by any authorized fnmatch pattern."""
    remaining: list[str] = []
    for rel in manifest_changed_paths(before, after):
        allowed = False
        for pat in authorized_patterns:
            if fnmatch.fnmatch(rel, pat):
                allowed = True
                break
        if not allowed:
            remaining.append(rel)
    return remaining

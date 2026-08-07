"""File scanner and git reader — deterministic repository intelligence."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from forge.context.models import ContextError, FileEntry, GitInfo
from forge.context.hasher import hash_file
from forge.context.budget import load_content, MAX_TOTAL_CONTENT

# v1 extension → language mapping.  Lightweight, no pygments.
EXT_LANGUAGE = {
    ".py": "python",
    ".rs": "rust",
    ".java": "java",
    ".ts": "typescript",
    ".js": "javascript",
    ".go": "go",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".toml": "toml",
    ".md": "markdown",
    ".txt": "text",
    ".sh": "shell",
    ".bash": "shell",
    ".flow": "flow",
    ".block": "block",
    ".vasm": "vasm",
}

# Extensions we care about for engineering context.
CODE_EXTENSIONS = set(EXT_LANGUAGE.keys())

# Directories to exclude.
EXCLUDED_DIRS = {
    ".git", "__pycache__", "node_modules", "_archive",
    "dist", "build", ".forge", "target", ".mypy_cache",
    ".pytest_cache", ".ruff_cache",
}


def _is_hidden(path: Path) -> bool:
    """Check any component starts with '.' (except repo root)."""
    return any(part.startswith(".") for part in path.parts)


def _language(path: str) -> str | None:
    suffix = Path(path).suffix.lower()
    return EXT_LANGUAGE.get(suffix)


def read_git(repo_path: str) -> GitInfo:
    """Read git metadata using --porcelain for stability."""
    commit = None
    branch = None
    dirty = False
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_path, stderr=subprocess.DEVNULL,
        ).decode().strip()
    except Exception:
        pass
    try:
        branch = subprocess.check_output(
            ["git", "branch", "--show-current"],
            cwd=repo_path, stderr=subprocess.DEVNULL,
        ).decode().strip()
    except Exception:
        pass
    try:
        status = subprocess.check_output(
            ["git", "status", "--porcelain"],
            cwd=repo_path, stderr=subprocess.DEVNULL,
        ).decode().strip()
        dirty = bool(status)
    except Exception:
        pass
    return GitInfo(commit=commit or None, branch=branch or None, dirty=dirty)


def scan_files(
    repo_path: str,
    extra_extensions: set[str] | None = None,
) -> tuple[list[FileEntry], list[ContextError]]:
    """Scan repo for engineering files.  Deterministic order.

    Fatal only if repo_path is missing or not a directory.
    All per-file errors collected into errors list.
    """
    if not os.path.isdir(repo_path):
        raise FileNotFoundError(f"Repository path not found: {repo_path}")

    extensions = CODE_EXTENSIONS.copy()
    if extra_extensions:
        extensions.update(extra_extensions)

    files: list[FileEntry] = []
    errors: list[ContextError] = []

    # Deterministic walk: sorted dirs, sorted files within each dir.
    for root, dirs, filenames in os.walk(repo_path):
        # Prune excluded dirs in-place for efficiency.
        dirs[:] = sorted(d for d in dirs if d not in EXCLUDED_DIRS and not d.startswith("."))
        dirs.sort()

        rel_root = os.path.relpath(root, repo_path)
        if rel_root == ".":
            rel_root = ""

        for name in sorted(filenames):
            if name.startswith("."):
                continue
            full_path = os.path.join(root, name)
            rel_path = os.path.join(rel_root, name) if rel_root else name

            suffix = Path(name).suffix.lower()
            if suffix not in extensions:
                continue

            try:
                size = os.path.getsize(full_path)
            except OSError as e:
                errors.append(ContextError(rel_path, f"stat_failed: {e}"))
                continue

            try:
                fhash = hash_file(full_path)
            except Exception as e:
                errors.append(ContextError(rel_path, f"hash_failed: {e}"))
                continue

            lang = _language(rel_path)
            files.append(FileEntry(
                path=rel_path,
                size=size,
                hash=fhash,
                language=lang,
                content=None,  # filled later by budget
            ))

    return files, errors


def load_contents(
    files: list[FileEntry],
    repo_path: str,
) -> None:
    """Load file contents within budget, mutating FileEntry.content in place.

    Files sorted by size descending — large core modules loaded first.
    """
    remaining = MAX_TOTAL_CONTENT
    sorted_files = sorted(files, key=lambda f: f.size, reverse=True)
    for entry in sorted_files:
        full_path = os.path.join(repo_path, entry.path)
        content = load_content(full_path, entry.size, remaining)
        if content is not None:
            entry.content = content
            remaining -= len(content.encode("utf-8"))

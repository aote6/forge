"""build_context() — sole runtime API for RepositoryContext."""

from __future__ import annotations

from forge.context.errors import ContextFatalError
from forge.context.hasher import compute_tree_hash
from forge.context.models import RepositoryContext
from forge.context.scanner import load_contents, read_git, scan_files


def build_context(
    repo_path: str,
    *,
    include_content: bool = True,
    extra_extensions: set[str] | None = None,
) -> RepositoryContext:
    """Build a deterministic, machine-verifiable repository snapshot.

    Fatal only if repo_path missing or not a directory.
    All per-file errors are collected in context.errors — never silent.
    """
    import os
    if not os.path.exists(repo_path):
        raise ContextFatalError(f"Repository path does not exist: {repo_path}")
    if not os.path.isdir(repo_path):
        raise ContextFatalError(f"Repository path is not a directory: {repo_path}")

    abs_path = os.path.abspath(os.path.expanduser(repo_path))
    git = read_git(abs_path)
    files, errors = scan_files(abs_path, extra_extensions=extra_extensions)

    if include_content:
        load_contents(files, abs_path)

    tree_hash = compute_tree_hash(files)

    return RepositoryContext(
        schema_version="1.0",
        repo_path=abs_path,
        git=git,
        files=files,
        tree_hash=tree_hash,
        errors=errors,
    )

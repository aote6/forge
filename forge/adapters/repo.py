"""Repo adapter — local repository facts (no external repository tooling).

RepoContext is built from forge.context (snapshot + git metadata). It is
supplementary understanding; the authoritative machine facts come from
RepositorySnapshot / RepositoryIndex, which the orchestrator builds first.
"""
from __future__ import annotations

import subprocess

from forge.context.repository import build_context
from forge.protocols.models import RepoContext


def _git_changed_files(project_root: str) -> list[str]:
    """Changed files from `git status --porcelain`; empty when not a git repo."""
    try:
        out = subprocess.check_output(
            ["git", "status", "--porcelain"],
            cwd=project_root,
            stderr=subprocess.DEVNULL,
        ).decode()
    except Exception:
        return []
    changed: list[str] = []
    for line in out.splitlines():
        # porcelain v1: "XY <path>" or "XY <old> -> <new>" (rename)
        path = line[3:].split(" -> ")[-1].strip().strip('"')
        if path:
            changed.append(path)
    return changed


def get_repo_context(project_root: str) -> RepoContext:
    """Return RepoContext from local repository facts."""
    ctx = build_context(project_root, include_content=False)
    commit = (ctx.git.commit or "") if ctx.git else ""
    branch = (ctx.git.branch or "") if ctx.git else ""
    file_tree = [f.path for f in ctx.files]
    return RepoContext(
        repo_id=ctx.repo_path,
        commit_hash=commit,
        branch=branch,
        file_tree=file_tree,
        changed_files=_git_changed_files(project_root),
        recent_changes=[],
        status_excerpt=None,
    )

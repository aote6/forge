"""Repo adapter — protocol conversion only; Hub invokes zhiwang."""
from __future__ import annotations

from forge.adapters.hub_client import HubClient
from forge.protocols.models import RepoContext


def get_repo_context(project_root: str, hub: HubClient | None = None) -> RepoContext:
    """Return RepoContext via Hub capability 'zhiwang' / action 'repo_context'."""
    client = hub or HubClient(project_root=project_root)
    resp = client.invoke(
        capability="zhiwang",
        action="snapshot",
        payload={"path": project_root},
    )
    if not resp.ok:
        # Fallback: minimal local context so orchestrator can still plan offline
        return _local_fallback(project_root, error=resp.error)

    data = resp.data
    return RepoContext(
        repo_id=data.get("repo_id") or project_root,
        commit_hash=data.get("commit_hash", ""),
        branch=data.get("branch", ""),
        file_tree=list(data.get("file_tree") or []),
        changed_files=list(data.get("changed_files") or []),
        recent_changes=list(data.get("recent_changes") or []),
        status_excerpt=data.get("status_excerpt"),
    )


def _local_fallback(project_root: str, error: str = "") -> RepoContext:
    import os
    tree = []
    for root, dirs, files in os.walk(project_root):
        dirs[:] = [d for d in dirs if d not in {".git", ".forge", "__pycache__", "node_modules"}]
        for f in files:
            rel = os.path.relpath(os.path.join(root, f), project_root)
            tree.append(rel)
            if len(tree) >= 500:
                break
        if len(tree) >= 500:
            break
    return RepoContext(
        repo_id=project_root,
        commit_hash="",
        file_tree=tree,
        status_excerpt=f"hub/zhiwang unavailable: {error}" if error else "local fallback",
    )

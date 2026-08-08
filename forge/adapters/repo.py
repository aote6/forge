"""Repo adapter — protocol conversion only; Hub invokes zhiwang."""
from __future__ import annotations

from forge.adapters.hub_client import HubClient
from forge.protocols.models import RepoContext


def get_repo_context(project_root: str, hub: HubClient | None = None) -> RepoContext:
    """Return RepoContext via Hub capability 'zhiwang'. Hub failure fails the task.

    No local os.walk fallback — Hub is the sole production tool entry.
    """
    client = hub or HubClient(project_root=project_root)
    resp = client.invoke(
        capability="zhiwang",
        action="snapshot",
        payload={"path": project_root},
    )
    if not resp.ok:
        raise RuntimeError(
            f"Hub zhiwang unavailable — cannot build RepoContext: {resp.error}"
        )

    data = resp.data.get("data", {}) if isinstance(resp.data, dict) else {}
    return RepoContext(
        repo_id=data.get("repo_id") or project_root,
        commit_hash=data.get("commit_hash", ""),
        branch=data.get("branch", ""),
        file_tree=list(data.get("file_tree") or []),
        changed_files=list(data.get("changed_files") or []),
        recent_changes=list(data.get("recent_changes") or []),
        status_excerpt=data.get("status_excerpt"),
    )

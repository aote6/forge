"""Repository Snapshot — machine-verifiable identity for Plan binding.

snapshot_id == tree_hash from forge.context.build_context (include_content=False).

tree_hash coverage (from scanner.scan_files + hasher.compute_tree_hash):
  - Working-tree files under repo_path with known code extensions
  - Excludes EXCLUDED_DIRS (.git, node_modules, target, .forge, ...)
  - Excludes hidden files/dirs (name starts with '.')
  - Content SHA-256 per file, then sorted path:hash lines → SHA-256

Not a full git tree object. Detects source-file changes that affect
engineering plans between PLAN and EXECUTE.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Optional

from forge.context.repository import build_context

# Process-local cache: tree_hash → RepositorySnapshot.
# tree_hash still requires a scan; we only reuse the Snapshot object.
_snapshot_cache: dict[str, "RepositorySnapshot"] = {}


@dataclass(frozen=True)
class RepositorySnapshot:
    """Lightweight identity of repository state for Plan/Checkpoint binding."""

    snapshot_id: str  # == tree_hash
    tree_hash: str
    commit_hash: str = ""
    branch: str = ""
    file_count: int = 0
    dirty: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "tree_hash": self.tree_hash,
            "commit_hash": self.commit_hash,
            "branch": self.branch,
            "file_count": self.file_count,
            "dirty": self.dirty,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "RepositorySnapshot":
        th = data.get("tree_hash") or data.get("snapshot_id") or ""
        return cls(
            snapshot_id=data.get("snapshot_id") or th,
            tree_hash=th,
            commit_hash=data.get("commit_hash") or "",
            branch=data.get("branch") or "",
            file_count=int(data.get("file_count") or 0),
            dirty=bool(data.get("dirty") or False),
        )


def take_snapshot(repo_path: str) -> RepositorySnapshot:
    """Compute current repository snapshot (no file content loading).

    Process-local cache keyed by tree_hash: after the scan that produces
    tree_hash, the resulting RepositorySnapshot is reused on subsequent
    calls that yield the same tree_hash. Semantics and fields unchanged.
    """
    ctx = build_context(repo_path, include_content=False)
    th = ctx.tree_hash
    cached = _snapshot_cache.get(th)
    if cached is not None:
        return cached
    commit = (ctx.git.commit or "") if ctx.git else ""
    branch = (ctx.git.branch or "") if ctx.git else ""
    dirty = bool(ctx.git.dirty) if ctx.git else False
    snap = RepositorySnapshot(
        snapshot_id=th,
        tree_hash=th,
        commit_hash=commit,
        branch=branch,
        file_count=len(ctx.files),
        dirty=dirty,
    )
    _snapshot_cache[th] = snap
    return snap


class StaleSnapshotError(Exception):
    """Plan snapshot does not match current repository state."""

    def __init__(
        self,
        planned_id: str,
        current_id: str,
        message: str = "",
    ):
        self.planned_id = planned_id
        self.current_id = current_id
        self.code = "STALE_SNAPSHOT"
        msg = message or (
            f"STALE_SNAPSHOT: plan snapshot {planned_id[:16]}... "
            f"!= current {current_id[:16]}..."
        )
        super().__init__(msg)


def assert_snapshot_match(
    planned_snapshot_id: Optional[str],
    repo_path: str,
) -> RepositorySnapshot:
    """Recompute snapshot; raise StaleSnapshotError if plan is stale.

    Empty planned_snapshot_id is treated as invalid (fail-closed).
    """
    current = take_snapshot(repo_path)
    if not planned_snapshot_id:
        raise StaleSnapshotError(
            planned_id="",
            current_id=current.snapshot_id,
            message="STALE_SNAPSHOT: plan has no snapshot_id binding",
        )
    if planned_snapshot_id != current.snapshot_id:
        raise StaleSnapshotError(
            planned_id=planned_snapshot_id,
            current_id=current.snapshot_id,
        )
    return current

"""GitProjection — 世界状态变更同步到 Git。

只提交 TransactionDelta 涉及的文件，不做 git add -A。
失败不影响世界事务。
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from forge.projections.base import Projection, ProjectionResult, TransactionDelta
from forge.world.types import Receipt


class GitProjection(Projection):
    """Git 投影。事务提交后仅同步 delta 涉及的文件。"""

    def __init__(self, project_root: str = "."):
        self.project_root = os.path.abspath(os.path.expanduser(project_root))
        self._enabled = self._is_git_repo()

    @property
    def name(self) -> str:
        return "git"

    @property
    def enabled(self) -> bool:
        return self._enabled

    def _is_git_repo(self) -> bool:
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--git-dir"],
                cwd=self.project_root,
                capture_output=True,
                text=True,
                timeout=5,
            )
            return result.returncode == 0
        except Exception:
            return False

    def _git(self, *args: str) -> tuple[int, str, str]:
        try:
            result = subprocess.run(
                ["git"] + list(args),
                cwd=self.project_root,
                capture_output=True,
                text=True,
                timeout=30,
            )
            return result.returncode, result.stdout.strip(), result.stderr.strip()
        except Exception as e:
            return -1, "", str(e)

    def _paths_from_delta(self, delta: TransactionDelta) -> list[str]:
        paths: list[str] = []
        for object_id, writes in delta.memory_written.items():
            for state_id, value in writes:
                if state_id == 0 and value:
                    p = Path(value)
                    if not p.is_absolute():
                        p = Path(self.project_root) / p
                    paths.append(str(p))
        meta_paths = (delta.metadata or {}).get("deleted_paths", {})
        for path in meta_paths.values():
            p = Path(str(path))
            if not p.is_absolute():
                p = Path(self.project_root) / p
            paths.append(str(p))
        # unique, preserve order
        seen = set()
        unique = []
        for p in paths:
            if p not in seen:
                seen.add(p)
                unique.append(p)
        return unique

    def prepare(self, delta: TransactionDelta) -> dict | None:
        if not self._enabled:
            return None
        paths = self._paths_from_delta(delta)
        if not paths:
            return None
        return {
            "type": "git_status",
            "files_to_commit": paths,
        }

    def apply(self, receipt: Receipt, delta: TransactionDelta) -> ProjectionResult:
        if not self._enabled:
            return ProjectionResult(name=self.name, success=True, reason="git disabled")

        paths = self._paths_from_delta(delta)
        if not paths:
            return ProjectionResult(name=self.name, success=True, reason="no paths in delta")

        try:
            for path in paths:
                # path relative to project root preferred for git
                try:
                    rel = str(Path(path).relative_to(self.project_root))
                except ValueError:
                    rel = path
                code, _, err = self._git("add", "--", rel)
                if code != 0 and err:
                    # file may already be deleted; still try
                    pass

            code, _, _ = self._git("diff", "--cached", "--quiet")
            if code == 0:
                return ProjectionResult(name=self.name, success=True, reason="nothing to commit")

            commit_msg = (
                f"forge: tx={receipt.tx_id} v={receipt.version}\n"
                f"\n"
                f"Objects created: {len(delta.objects_created)}\n"
                f"Objects deleted: {len(delta.objects_deleted)}\n"
                f"Objects frozen: {len(delta.objects_frozen)}\n"
                f"Links added: {len(delta.links_added)}\n"
                f"Links removed: {len(delta.links_removed)}"
            )
            code, _, err = self._git("commit", "-m", commit_msg)
            if code != 0:
                return ProjectionResult(
                    name=self.name,
                    success=False,
                    reason=err or f"git commit exit {code}",
                    retryable=True,
                )
            return ProjectionResult(name=self.name, success=True, reason="committed")
        except Exception as e:
            return ProjectionResult(
                name=self.name,
                success=False,
                reason=f"{type(e).__name__}: {e}",
                retryable=True,
            )

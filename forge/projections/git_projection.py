"""GitProjection — 世界状态变更的 Git 观察投影。

自动事务提交已停用：不再在每笔事务完成时 git commit。
文件变更保留在工作区，由用户手动 git add / git commit。
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from forge.projections.base import Projection, ProjectionResult, TransactionDelta
from forge.world.types import Receipt


class GitProjection(Projection):
    """Git 投影。事务提交后不再自动 commit，只报告变更文件。"""

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

    def _paths_from_delta(self, delta: TransactionDelta) -> list[str]:
        paths: list[str] = []
        # memory_written is a list of dicts: [{'object_id': N, 'state_id': N, 'value_hex': '...'}, ...]
        for w in (delta.memory_written or []):
            if isinstance(w, dict):
                state_id = w.get("state_id")
                value_hex = w.get("value_hex")
                if state_id == 0 and value_hex:
                    try:
                        value = bytes.fromhex(value_hex).decode("utf-8")
                    except (ValueError, UnicodeDecodeError):
                        continue
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
            "files_changed": paths,
            "auto_commit": False,
        }

    def apply(self, receipt: Receipt, delta: TransactionDelta) -> ProjectionResult:
        if not self._enabled:
            return ProjectionResult(name=self.name, success=True, reason="git disabled")

        paths = self._paths_from_delta(delta)
        if not paths:
            return ProjectionResult(name=self.name, success=True, reason="no paths in delta")

        # 自动事务提交已停用：不 stage、不 commit，变更留在工作区由用户手动提交。
        return ProjectionResult(
            name=self.name,
            success=True,
            reason=f"auto-commit disabled: {len(paths)} file(s) changed, commit manually",
        )

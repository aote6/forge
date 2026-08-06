"""GitProjection — 世界状态变更自动同步到 Git。

commit 成功后自动执行 git add + git commit。
失败不影响世界事务。
"""

from __future__ import annotations

import subprocess
import os
from pathlib import Path

from forge.projections.base import Projection, TransactionDelta
from forge.world.types import Receipt


class GitProjection(Projection):
    """Git 投影。事务提交后自动同步到 Git 仓库。"""

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

    def prepare(self, delta: TransactionDelta) -> dict | None:
        """列出将要 commit 的文件变更。"""
        if not self._enabled:
            return None

        # 只看已修改但未暂存的文件
        code, stdout, stderr = self._git("diff", "--name-only")
        staged_code, staged_out, _ = self._git("diff", "--cached", "--name-only")

        files = []
        if code == 0 and stdout:
            files.extend(stdout.split("\n"))
        if staged_code == 0 and staged_out:
            files.extend(staged_out.split("\n"))

        if not files:
            return None

        return {
            "type": "git_status",
            "files_to_commit": list(set(files)),
        }

    def apply(self, receipt: Receipt, delta: TransactionDelta) -> None:
        """git add + git commit 所有变更。"""
        if not self._enabled:
            return

        # git add -A 暂存所有变更
        self._git("add", "-A")

        # 检查是否有东西可提交
        code, stdout, _ = self._git("diff", "--cached", "--quiet")
        if code == 0:
            return  # 没有变更

        # git commit
        commit_msg = (
            f"forge: tx={receipt.tx_id} v={receipt.version}\n"
            f"\n"
            f"Objects created: {len(delta.objects_created)}\n"
            f"Objects deleted: {len(delta.objects_deleted)}\n"
            f"Objects frozen: {len(delta.objects_frozen)}\n"
            f"Links added: {len(delta.links_added)}\n"
            f"Links removed: {len(delta.links_removed)}"
        )
        self._git("commit", "-m", commit_msg)

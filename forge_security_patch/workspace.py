"""Workspace - 本地只读文件/代码操作入口。不包含事务。"""

from __future__ import annotations

import os
from pathlib import Path

from forge.core.file_manager import FileManager
from forge.core.indexer import AutoIndexer, BaseIndexer
from forge.core.security import is_blocked_path


class Workspace:
    def __init__(self, project_root: str = "."):
        self.project_root = str(Path(project_root).expanduser().resolve(strict=False))
        self.fm = FileManager()
        self.indexer: BaseIndexer = AutoIndexer()

    def _resolve(self, path: str) -> str:
        """解析路径：expanduser → resolve（跟 symlink）→ 必须在 project_root 内 → 黑名单。"""
        p = Path(os.path.expanduser(str(path)))
        candidate = p if p.is_absolute() else Path(self.project_root) / p
        try:
            resolved = candidate.resolve(strict=False)
        except (OSError, RuntimeError) as e:
            raise PermissionError(f"无法解析路径: {path}: {e}") from e

        root = Path(self.project_root)
        try:
            resolved.relative_to(root)
        except ValueError:
            raise PermissionError(
                f"路径逃逸 workspace: {resolved} (root={self.project_root})"
            )

        blocked = is_blocked_path(str(resolved))
        if blocked:
            raise PermissionError(
                f"路径被安全策略拦截（命中规则: {blocked}）: {resolved}"
            )
        return str(resolved)

    def read_file(self, path: str, start: int = 1, end: int = 0) -> str:
        path = self._resolve(path)
        if end == 0:
            return self.fm.read(path)
        return self.fm.read_lines(path, start, end)

    def search_code(self, pattern: str, path: str = ".") -> str:
        return self.indexer.search(pattern, self._resolve(path))

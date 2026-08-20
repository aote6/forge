"""Workspace - 本地只读文件/代码操作入口。不包含事务。"""

import os
from pathlib import Path
from forge.core.file_manager import FileManager
from forge.core.indexer import AutoIndexer, BaseIndexer
from forge.core.security import is_blocked_path


class Workspace:
    def __init__(self, project_root: str = "."):
        self.project_root = os.path.abspath(os.path.expanduser(project_root))
        self.fm = FileManager()
        self.indexer: BaseIndexer = AutoIndexer()

    def _resolve(self, path: str) -> str:
        p = Path(os.path.expanduser(path))
        resolved = p if p.is_absolute() else Path(self.project_root) / p
        # 禁止访问 workspace 之外的任意路径（home、/etc、/data 等）
        try:
            resolved.relative_to(self.project_root)
        except ValueError:
            raise PermissionError(f"路径逃逸 workspace: {resolved} (root={self.project_root})")
        blocked = is_blocked_path(str(resolved))
        if blocked:
            raise PermissionError(f"路径被安全策略拦截（命中规则: {blocked}）: {resolved}")
        return str(resolved)

    def read_file(self, path: str, start: int = 1, end: int = 0) -> str:
        path = self._resolve(path)
        if end == 0:
            return self.fm.read(path)
        return self.fm.read_lines(path, start, end)

    def search_code(self, pattern: str, path: str = ".") -> str:
        return self.indexer.search(pattern, self._resolve(path))

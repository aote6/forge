"""FileProjection — 世界状态在本地文件系统上的投影。

负责：
- 文件创建、更新、删除
- diff 生成
- 备份
- 语法校验
- 恢复
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Optional

from forge.projections.base import Projection, TransactionDelta
from forge.core.file_manager import FileManager
from forge.core.patch_engine import PatchEngine
from forge.core.validator import ValidatorRegistry
from forge.core.backup_manager import BackupManager
from forge.world.types import Receipt


class FileProjection(Projection):
    """文件系统投影。"""

    def __init__(self, project_root: str = "."):
        self.project_root = os.path.abspath(os.path.expanduser(project_root))
        self.fm = FileManager()
        self.patch_engine = PatchEngine()
        self.backup = BackupManager()

    @property
    def name(self) -> str:
        return "file"

    def _resolve(self, path: str) -> str:
        p = Path(os.path.expanduser(path))
        return str(p if p.is_absolute() else Path(self.project_root) / p)

    def prepare(self, delta: TransactionDelta) -> Optional[dict]:
        """生成 diff 预览供用户确认。"""
        diffs = {}

        for object_id, writes in delta.memory_written.items():
            for state_id, content in writes:
                path = self._object_path(object_id, state_id)
                if path:
                    try:
                        original = self.fm.read(path)
                    except FileNotFoundError:
                        original = ""
                    patch = self.patch_engine.diff(original, content, path)
                    diffs[path] = patch

        if not diffs:
            return None

        return {
            "type": "diff_preview",
            "diffs": diffs,
            "requires_confirmation": True
        }

    def apply(self, receipt: Receipt, delta: TransactionDelta) -> None:
        """应用世界状态变更到文件系统。"""
        for object_id, writes in delta.memory_written.items():
            for state_id, content in writes:
                path = self._object_path(object_id, state_id)
                if not path:
                    continue

                # 备份原文件
                try:
                    self.backup.backup(path)
                except Exception:
                    pass

                # 写入新内容
                self.fm.write(path, content)

                # 语法校验
                ok, msg = ValidatorRegistry.validate(path)
                if not ok:
                    # 校验失败，回滚
                    self.backup.restore_latest(path)
                    raise RuntimeError(f"语法校验失败: {msg}")

    def _object_path(self, object_id: int, state_id: int) -> Optional[str]:
        """从 object_id + state_id 推导文件路径。

        这是一个临时实现，未来应通过 Object 的 metadata 或 Link 关系确定路径。
        state_id 为 0 表示该 Object 的主文件路径（通过 metadata 中的 path 确定）。
        """
        # 临时：通过项目根目录下的 .forge/path_map 查找
        # 未来这应该来自 Veritas Object 的 metadata
        return None  # 需要 WorldSession 提供 object→path 映射

"""FileProjection — 世界状态在本地文件系统上的投影。

路径映射策略：
- state_id=0 存储文件路径（相对于项目根目录）
- state_id=1 存储文件内容
- 未来可从 Veritas Object metadata 获取更丰富的映射
"""

from __future__ import annotations

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
    """文件系统投影。

    把 Veritas 世界状态变更投影到本地文件系统。
    """

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

    # ── Projection 接口 ──────────────────────────────────────

    def prepare(self, delta: TransactionDelta) -> Optional[dict]:
        """生成 diff 预览供用户确认。

        遍历 delta 中的所有写入，对比当前文件内容生成 unified diff。
        返回 None 表示不需要确认，返回 dict 表示需要用户确认。
        """
        diffs = {}
        files_created = []
        files_modified = []

        for object_id, writes in delta.memory_written.items():
            path = self._resolve_path_from_writes(writes)
            if path is None:
                continue

            content = self._extract_content(writes)
            if content is None:
                continue

            if self.fm.exists(path):
                try:
                    original = self.fm.read(path)
                    patch = self.patch_engine.diff(original, content, path)
                    if patch.strip():
                        diffs[path] = patch
                        files_modified.append(path)
                except Exception:
                    files_modified.append(path)
            else:
                files_created.append(path)
                # 新文件：展示内容预览
                preview = content[:500]
                if len(content) > 500:
                    preview += "\n...(truncated)"
                diffs[path] = preview

        if not diffs and not delta.objects_created and not delta.objects_deleted:
            return None

        return {
            "type": "diff_preview",
            "diffs": diffs,
            "files_created": files_created,
            "files_modified": files_modified,
            "objects_created": delta.objects_created,
            "objects_deleted": delta.objects_deleted,
            "requires_confirmation": True,
        }

    def apply(self, receipt: Receipt, delta: TransactionDelta) -> None:
        """应用世界状态变更到文件系统。

        对每个 object 的写入：
        1. 备份原文件（如果存在）
        2. 写入新内容
        3. 语法校验
        4. 校验失败则回滚
        """
        applied = []

        for object_id, writes in delta.memory_written.items():
            path = self._resolve_path_from_writes(writes)
            if path is None:
                continue

            content = self._extract_content(writes)
            if content is None:
                continue

            # 备份原文件
            if self.fm.exists(path):
                try:
                    self.backup.backup(path)
                except Exception:
                    pass

            # 确保目录存在
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

            # 写入新内容
            self.fm.write(path, content)
            applied.append(path)

            # 语法校验
            ok, msg = ValidatorRegistry.validate(path)
            if not ok:
                # 校验失败，回滚所有已写入的文件
                for p in applied:
                    try:
                        self.backup.restore_latest(p)
                    except Exception:
                        pass
                raise RuntimeError(f"语法校验失败: {path}: {msg}")

        # 处理删除
        for object_id in delta.objects_deleted:
            path = self._path_for_object(object_id)
            if path and self.fm.exists(path):
                try:
                    self.backup.backup(path)
                except Exception:
                    pass
                os.remove(path)

    # ── 路径映射 ─────────────────────────────────────────────

    def _resolve_path_from_writes(self, writes: list[tuple[int, str]]) -> Optional[str]:
        """从 writes 列表中提取文件路径（state_id=0 的值）。"""
        for state_id, value in writes:
            if state_id == 0:
                return self._resolve(value)
        return None

    def _extract_content(self, writes: list[tuple[int, str]]) -> Optional[str]:
        """从 writes 列表中提取文件内容（state_id=1 的值）。"""
        for state_id, value in writes:
            if state_id == 1:
                return value
        return None

    def _path_for_object(self, object_id: int) -> Optional[str]:
        """根据 object_id 查找对应文件路径。

        当前实现：从本地缓存查找。未来应从 Veritas Object metadata 获取。
        """
        return None  # 需要 Object→path 映射表，后续 Phase 实现

    def set_path_mapping(self, object_id: int, path: str) -> None:
        """注册 object_id 到文件路径的映射。

        供 WorldRuntime 在 create_object 后调用。
        """
        if not hasattr(self, '_path_map'):
            self._path_map: dict[int, str] = {}
        self._path_map[object_id] = self._resolve(path)

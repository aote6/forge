"""FileProjection — 世界状态在本地文件系统上的投影。

路径与内容来自 TransactionDelta.memory_written：
- state_id=0: 文件路径（相对项目根目录）
- state_id=1: 文件内容
- state_id=2: 修改操作（JSON 序列化的 operations 列表）

不持久化 object→path 映射，不成为世界状态源。路径仅从 delta 重建。
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

from forge.projections.base import Projection, ProjectionResult, TransactionDelta
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
        diffs = {}
        files_created = []
        files_modified = []
        files_deleted = []

        for object_id, writes in delta.memory_written.items():
            path = self._path_from_writes(writes)
            if path is None:
                continue

            content = self._extract_content(writes)
            operations = self._extract_operations(writes)

            if self.fm.exists(path):
                try:
                    original = self.fm.read(path)
                except Exception:
                    original = ""

                if operations:
                    new_content = self.patch_engine.apply_edits(
                        original, self._dicts_to_edits(operations)
                    )
                    patch = self.patch_engine.diff(original, new_content, path)
                elif content is not None:
                    patch = self.patch_engine.diff(original, content, path)
                else:
                    patch = ""

                if patch.strip():
                    diffs[path] = patch
                    files_modified.append(path)
            else:
                files_created.append(path)
                preview = (content or "")[:500]
                if len(content or "") > 500:
                    preview += "\n...(truncated)"
                diffs[path] = preview

        for object_id in delta.objects_deleted:
            path = self._path_for_deleted(object_id, delta)
            if path:
                files_deleted.append(path)
                if path not in diffs:
                    diffs[path] = "[文件将被删除]"

        if not diffs and not delta.objects_created and not delta.objects_deleted:
            return None

        return {
            "type": "diff_preview",
            "diffs": diffs,
            "files_created": files_created,
            "files_modified": files_modified,
            "files_deleted": files_deleted,
            "objects_created": delta.objects_created,
            "objects_deleted": delta.objects_deleted,
            "requires_confirmation": True,
        }

    def apply(self, receipt: Receipt, delta: TransactionDelta) -> ProjectionResult:
        applied: list[str] = []
        try:
            for object_id, writes in delta.memory_written.items():
                path = self._path_from_writes(writes)
                if path is None:
                    continue

                content = self._extract_content(writes)
                operations = self._extract_operations(writes)

                original = ""
                if self.fm.exists(path):
                    try:
                        original = self.fm.read(path)
                    except Exception:
                        pass
                    try:
                        self.backup.backup(path)
                    except Exception:
                        pass

                if operations:
                    new_content = self.patch_engine.apply_edits(
                        original, self._dicts_to_edits(operations)
                    )
                elif content is not None:
                    new_content = content
                else:
                    continue

                os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
                self.fm.write(path, new_content)
                applied.append(path)

                ok, msg = ValidatorRegistry.validate(path)
                if not ok:
                    for p in applied:
                        try:
                            self.backup.restore_latest(p)
                        except Exception:
                            pass
                    return ProjectionResult(
                        name=self.name,
                        success=False,
                        reason=f"语法校验失败: {path}: {msg}",
                        retryable=False,
                    )

            for object_id in delta.objects_deleted:
                path = self._path_for_deleted(object_id, delta)
                if path and self.fm.exists(path):
                    try:
                        self.backup.backup(path)
                    except Exception:
                        pass
                    os.remove(path)

            return ProjectionResult(name=self.name, success=True, reason="ok")
        except Exception as e:
            for p in applied:
                try:
                    self.backup.restore_latest(p)
                except Exception:
                    pass
            return ProjectionResult(
                name=self.name,
                success=False,
                reason=f"{type(e).__name__}: {e}",
                retryable=True,
            )

    def _path_from_writes(self, writes: list[tuple[int, str]]) -> Optional[str]:
        for state_id, value in writes:
            if state_id == 0 and value:
                return self._resolve(value)
        return None

    def _path_for_deleted(self, object_id: int, delta: TransactionDelta) -> Optional[str]:
        writes = delta.memory_written.get(object_id)
        if writes:
            return self._path_from_writes(writes)
        # path may be in metadata if provided by upper layer
        meta_paths = (delta.metadata or {}).get("deleted_paths", {})
        if object_id in meta_paths:
            return self._resolve(str(meta_paths[object_id]))
        return None

    def _extract_content(self, writes: list[tuple[int, str]]) -> Optional[str]:
        for state_id, value in writes:
            if state_id == 1:
                return value
        return None

    def _extract_operations(self, writes: list[tuple[int, str]]) -> Optional[list]:
        for state_id, value in writes:
            if state_id == 2:
                try:
                    return json.loads(value)
                except Exception:
                    return None
        return None

    def _dicts_to_edits(self, operations: list) -> list:
        from forge.core.patch_engine import EditOp
        edits = []
        for op in operations:
            if isinstance(op, EditOp):
                edits.append(op)
            else:
                edits.append(EditOp(
                    type=op.get("type", "replace"),
                    start_line=op.get("start_line", 0),
                    end_line=op.get("end_line", 0),
                    new_lines=op.get("new_lines", []),
                ))
        return edits

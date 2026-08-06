"""FileProjection — 世界状态在本地文件系统上的投影。

适配 veritasd v2 delta 格式：memory_written 为 [{"object_id","state_id","value_hex"},...]
"""

from __future__ import annotations

import json
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
    def __init__(self, project_root: str = ".", object_path_map=None):
        self.project_root = os.path.abspath(os.path.expanduser(project_root))
        self.fm = FileManager()
        self.patch_engine = PatchEngine()
        self.backup = BackupManager()
        self.object_path_map = object_path_map

    @property
    def name(self) -> str:
        return "file"

    def _resolve(self, path: str) -> str:
        p = Path(os.path.expanduser(path))
        return str(p if p.is_absolute() else Path(self.project_root) / p)

    def _get_path(self, writes: list) -> Optional[str]:
        """从 memory_written 中提取文件路径（state_id=0）。"""
        for w in writes:
            sid = w.get("state_id") if isinstance(w, dict) else w[0]
            val = w.get("value_hex") if isinstance(w, dict) else w[1]
            if sid == 0:
                try:
                    return self._resolve(bytes.fromhex(val).decode("utf-8"))
                except Exception:
                    return self._resolve(val)
        return None

    def _get_content(self, writes: list) -> Optional[str]:
        """从 memory_written 中提取文件内容（state_id=1）。"""
        for w in writes:
            sid = w.get("state_id") if isinstance(w, dict) else w[0]
            val = w.get("value_hex") if isinstance(w, dict) else w[1]
            if sid == 1:
                try:
                    return bytes.fromhex(val).decode("utf-8")
                except Exception:
                    return val
        return None

    def _get_operations(self, writes: list) -> Optional[list]:
        """从 memory_written 中提取修改操作（state_id=2）。"""
        for w in writes:
            sid = w.get("state_id") if isinstance(w, dict) else w[0]
            val = w.get("value_hex") if isinstance(w, dict) else w[1]
            if sid == 2:
                try:
                    raw = bytes.fromhex(val).decode("utf-8") if isinstance(w, dict) else val
                    return json.loads(raw)
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

    def _group_writes_by_object(self, delta: TransactionDelta) -> dict[int, list]:
        """将 memory_written 按 object_id 分组。"""
        groups: dict[int, list] = {}
        for w in delta.memory_written:
            oid = w.get("object_id") if isinstance(w, dict) else w[0]
            if oid not in groups:
                groups[oid] = []
            groups[oid].append(w)
        return groups

    def prepare(self, delta: TransactionDelta) -> Optional[dict]:
        diffs = {}
        files_created = []
        files_modified = []
        files_deleted = []

        for object_id, writes in self._group_writes_by_object(delta).items():
            path = self._get_path(writes)
            if path is None:
                continue

            content = self._get_content(writes)
            operations = self._get_operations(writes)

            if self.fm.exists(path):
                try:
                    original = self.fm.read(path)
                except Exception:
                    original = ""
                if operations:
                    new_content = self.patch_engine.apply_edits(original, self._dicts_to_edits(operations))
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
            path = self._path_for_object(object_id)
            if path:
                files_deleted.append(path)
                diffs[path] = "[文件将被删除]"

        if not diffs:
            return None

        return {
            "type": "diff_preview",
            "diffs": diffs,
            "files_created": files_created,
            "files_modified": files_modified,
            "files_deleted": files_deleted,
            "requires_confirmation": True,
        }

    def apply(self, receipt: Receipt, delta: TransactionDelta):
        applied = []

        for object_id, writes in self._group_writes_by_object(delta).items():
            path = self._get_path(writes)
            if path is None:
                continue

            content = self._get_content(writes)
            operations = self._get_operations(writes)

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
                new_content = self.patch_engine.apply_edits(original, self._dicts_to_edits(operations))
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
                raise RuntimeError(f"语法校验失败: {path}: {msg}")

        for object_id in delta.objects_deleted:
            path = self._path_for_object(object_id)
            if path and self.fm.exists(path):
                try:
                    self.backup.backup(path)
                except Exception:
                    pass
                os.remove(path)

        from forge.projections.base import ProjectionResult
        return ProjectionResult(name=self.name, success=True)

    def _path_for_object(self, object_id: int) -> Optional[str]:
        if hasattr(self, "_path_map") and self._path_map is not None:
            return self.object_path_map.get(object_id)
        return None

    def set_path_mapping(self, object_id: int, path: str) -> None:
        pass

    def remove_path_mapping(self, object_id: int) -> None:
        pass

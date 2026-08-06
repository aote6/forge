"""FileProjection — 世界状态在本地文件系统上的投影。

路径映射：
- state_id=0: 文件路径（相对项目根目录）
- state_id=1: 文件内容
- state_id=2: 修改操作（JSON 序列化的 operations 列表）
- path_map 持久化到 .forge/object_map.json
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
    """文件系统投影。"""

    def __init__(self, project_root: str = "."):
        self.project_root = os.path.abspath(os.path.expanduser(project_root))
        self.fm = FileManager()
        self.patch_engine = PatchEngine()
        self.backup = BackupManager()
        self._path_map: dict[int, str] = {}
        self._map_file = Path(self.project_root) / ".forge" / "object_map.json"
        self._load_path_map()

    @property
    def name(self) -> str:
        return "file"

    def _resolve(self, path: str) -> str:
        p = Path(os.path.expanduser(path))
        return str(p if p.is_absolute() else Path(self.project_root) / p)

    # ── path_map 持久化 ─────────────────────────────────────

    def _load_path_map(self) -> None:
        try:
            if self._map_file.exists():
                with open(self._map_file) as f:
                    raw = json.load(f)
                    self._path_map = {int(k): v for k, v in raw.items()}
        except Exception:
            self._path_map = {}

    def _save_path_map(self) -> None:
        try:
            self._map_file.parent.mkdir(parents=True, exist_ok=True)
            with open(self._map_file, "w") as f:
                json.dump(self._path_map, f, indent=2)
        except Exception:
            pass

    def set_path_mapping(self, object_id: int, path: str) -> None:
        self._path_map[object_id] = path
        self._save_path_map()

    def remove_path_mapping(self, object_id: int) -> None:
        self._path_map.pop(object_id, None)
        self._save_path_map()

    def _path_for_object(self, object_id: int) -> Optional[str]:
        path = self._path_map.get(object_id)
        if path:
            return self._resolve(path)
        return None

    # ── Projection 接口 ──────────────────────────────────────

    def prepare(self, delta: TransactionDelta) -> Optional[dict]:
        diffs = {}
        files_created = []
        files_modified = []
        files_deleted = []

        # 新建和修改
        for object_id, writes in delta.memory_written.items():
            path = self._path_for_object(object_id)
            if path is None:
                # 回退：从 writes 中提取路径
                path = self._resolve_path_from_writes(writes)
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
                    # modify_file: 预览应用 operations 后的结果
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

        # 删除
        for object_id in delta.objects_deleted:
            path = self._path_for_object(object_id)
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

    def apply(self, receipt: Receipt, delta: TransactionDelta) -> None:
        applied = []

        # 新建和修改
        for object_id, writes in delta.memory_written.items():
            path = self._path_for_object(object_id)
            if path is None:
                path = self._resolve_path_from_writes(writes)
            if path is None:
                continue

            content = self._extract_content(writes)
            operations = self._extract_operations(writes)

            # 读取原文件
            original = ""
            if self.fm.exists(path):
                try:
                    original = self.fm.read(path)
                except Exception:
                    pass
                # 备份
                try:
                    self.backup.backup(path)
                except Exception:
                    pass

            # 计算新内容
            if operations:
                new_content = self.patch_engine.apply_edits(original, self._dicts_to_edits(operations))
            elif content is not None:
                new_content = content
            else:
                continue

            # 写入
            os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
            self.fm.write(path, new_content)
            applied.append(path)

            # 语法校验
            ok, msg = ValidatorRegistry.validate(path)
            if not ok:
                for p in applied:
                    try:
                        self.backup.restore_latest(p)
                    except Exception:
                        pass
                raise RuntimeError(f"语法校验失败: {path}: {msg}")

        # 删除
        for object_id in delta.objects_deleted:
            path = self._path_for_object(object_id)
            if path and self.fm.exists(path):
                try:
                    self.backup.backup(path)
                except Exception:
                    pass
                os.remove(path)
                self.remove_path_mapping(object_id)

    # ── writes 解析 ──────────────────────────────────────────

    def _resolve_path_from_writes(self, writes: list[tuple[int, str]]) -> Optional[str]:
        for state_id, value in writes:
            if state_id == 0:
                return self._resolve(value)
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
        """把 LLM 传入的 dict 列表转为 PatchEngine 期望的 EditOp 列表。"""
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

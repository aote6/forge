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
        from forge.core.security import resolve_workspace_path
        return resolve_workspace_path(self.project_root, path)

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
        """Convert Intent-facing operation dicts into internal EditOp objects.

        Intent-level line numbers are 1-indexed and inclusive (e.g.
        start_line=1, end_line=1 means "line 1 only" — the natural way a
        human or LLM specifies "replace the first line"). EditOp/apply_edits
        internally use 0-indexed half-open ranges [start_line, end_line),
        matching difflib.get_opcodes() semantics used by compute_edits().
        This boundary is where that conversion must happen; patch_engine.py
        itself stays untouched and self-consistent.
        """
        from forge.core.patch_engine import EditOp
        edits = []
        for op in operations:
            if isinstance(op, EditOp):
                edits.append(op)
            else:
                if not isinstance(op, dict) or "start_line" not in op or "end_line" not in op:
                    raise ValueError(
                        f"Malformed edit operation — expected dict with "
                        f"'start_line'/'end_line'/'new_lines', got: {op!r}. "
                        f"(old_text/new_text format is NOT supported here; "
                        f"that belongs to lu_patch, not FileProjection.)"
                    )
                start_line = op["start_line"]
                end_line = op["end_line"]
                if not isinstance(start_line, int) or start_line < 1:
                    raise ValueError(
                        f"start_line must be a 1-indexed positive integer, got: {start_line!r}"
                    )
                if not isinstance(end_line, int) or end_line < start_line - 1:
                    raise ValueError(
                        f"end_line must be >= start_line - 1 (1-indexed, inclusive), got: "
                        f"start_line={start_line!r}, end_line={end_line!r}"
                    )
                edits.append(EditOp(
                    type=op.get("type", "replace"),
                    # 1-indexed inclusive -> 0-indexed half-open
                    start_line=start_line - 1,
                    end_line=end_line,
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
            path = self._path_for_object(object_id, delta)
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
        """Materialize delta onto the host filesystem.

        On failure: do NOT pretend the world rolled back. Raise so the caller
        can mark projection_failed and enter recovery. Optional local restore
        is best-effort only and never claims success after a partial apply.
        """
        from forge.projections.base import ProjectionResult

        applied: list[str] = []

        try:
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
                    # Best-effort local restore; world remains committed.
                    for p in applied:
                        try:
                            self.backup.restore_latest(p)
                        except Exception:
                            pass
                    raise RuntimeError(
                        f"projection_failed: syntax validation failed for {path}: {msg}"
                    )

            for object_id in delta.objects_deleted:
                path = self._path_for_object(object_id, delta)
                if path and self.fm.exists(path):
                    try:
                        self.backup.backup(path)
                    except Exception:
                        pass
                    os.remove(path)
                    applied.append(path)

            return ProjectionResult(name=self.name, success=True)
        except Exception as e:
            return ProjectionResult(
                name=self.name,
                success=False,
                reason=str(e),
                retryable=True,
            )

    def _path_for_object(
        self, object_id: int, delta: Optional[TransactionDelta] = None
    ) -> Optional[str]:
        """Resolve deleted object path from delta.metadata first, then path map."""
        if delta is not None:
            meta = getattr(delta, "metadata", None) or {}
            deleted = meta.get("deleted_paths") or {}
            # keys may be int or str after JSON round-trip
            if object_id in deleted:
                return self._resolve(str(deleted[object_id]))
            if str(object_id) in deleted:
                return self._resolve(str(deleted[str(object_id)]))

        path_map = self.object_path_map
        if path_map is None:
            return None
        if hasattr(path_map, "get"):
            p = path_map.get(object_id)
            if p:
                return self._resolve(str(p))
        return None

    def set_path_mapping(self, object_id: int, path: str) -> None:
        if self.object_path_map is not None and hasattr(self.object_path_map, "set"):
            self.object_path_map.set(object_id, path)

    def remove_path_mapping(self, object_id: int) -> None:
        if self.object_path_map is not None and hasattr(self.object_path_map, "remove"):
            self.object_path_map.remove(object_id)

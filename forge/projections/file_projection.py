"""FileProjection — 世界状态在本地文件系统上的投影。

适配 veritasd v2 delta 格式：memory_written 为 [{"object_id","state_id","value_hex"},...]

缺口 #4b：批量写盘前后 / 逐文件写前做 hash 快照校验；漂移则停止并回滚已写部分。
单文件写入经 FileManager 原子写（临时文件 + os.replace）。
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
from forge.sync.git_utils import hash_file
from forge.world.types import Receipt


class FileProjection(Projection):
    def __init__(self, project_root: str = ".", object_path_map=None, sync_state=None):
        self.project_root = os.path.abspath(os.path.expanduser(project_root))
        self.fm = FileManager()
        self.patch_engine = PatchEngine()
        backup_dir = os.path.join(self.project_root, ".forge", "backups")
        self.backup = BackupManager(backup_dir=backup_dir)
        self.object_path_map = object_path_map
        # 可选 SyncState：磁盘真正写盘成功后推进 disk_synced_version（规则 A）。
        # None 表示不跟踪同步水位（单测 / 无同步场景）。
        self.sync_state = sync_state

    @property
    def name(self) -> str:
        return "file"

    def _resolve(self, path: str) -> str:
        """Resolve a path recorded in Veritas state (state_id=0) to a host path.

        Relative paths are Forge/Intent-level navigation and stay confined to
        project_root via resolve_workspace_path (unchanged behavior).

        Absolute paths are authoritative content coming from Veritas'
        committed state — Veritas is the source of truth, and Projection's
        job is to materialize whatever it committed, not to re-litigate
        where that path lives. We still enforce the blocklist (ssh keys,
        credentials, etc.) but do not require containment under
        project_root for paths that are already absolute.
        """
        import os as _os
        from pathlib import Path as _Path
        from forge.core.security import resolve_workspace_path, is_blocked_path, PathSecurityError

        expanded = _Path(_os.path.expanduser(path))
        if expanded.is_absolute():
            resolved = str(expanded.resolve())
            blocked = is_blocked_path(resolved)
            if blocked:
                raise PathSecurityError(
                    f"路径被安全策略拦截（命中规则: {blocked}）: {resolved}"
                )
            return resolved
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
        """Convert Machine EditOp dicts into internal EditOp objects.

        Accepts ONLY the frozen machine schema:
          0-based half-open [start_line, end_line) + new_lines: list[str]
        No authoring conversion here — that is solely authoring_to_machine_ops.
        """
        from forge.core.patch_engine import EditOp
        from forge.core.edit_contract import EditContractError, validate_machine_op
        edits = []
        for op in operations:
            if isinstance(op, EditOp):
                edits.append(op)
                continue
            try:
                validate_machine_op(op)
            except EditContractError as e:
                raise ValueError(
                    f"FileProjection accepts Machine EditOp only "
                    f"(0-based half-open + new_lines; no new_text/old_text): {e}"
                ) from e
            edits.append(EditOp(
                type=op.get("type", "replace"),
                start_line=op["start_line"],
                end_line=op["end_line"],
                new_lines=list(op["new_lines"]),
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

        这是**用户明确授权后的正常投影**：把已提交事务的 delta 物化到磁盘。

        契约 §4：任何分叉 / 部分失败都不得标为成功、不得推进同步水位。
        因此本方法要么完整写盘并返回 success=True，要么返回 success=False。

        缺口 #4b：
        - 写前对已存在目标路径记录 hash 快照；
        - 逐文件写入前再验一次，漂移则停止并回滚已写部分；
        - 回滚失败的路径记入 uncertain_paths，并从 last_known_file_hashes 移除。

        磁盘真正写盘成功后，才推进 sync_state.disk_synced_version（规则 A）。
        """
        from forge.projections.base import ProjectionResult

        applied: list[str] = []
        deleted: list[str] = []
        # path -> hash at batch start (only for paths that existed)
        pre_hashes: dict[str, str | None] = {}

        try:
            groups = self._group_writes_by_object(delta)
            # ── 写前快照 ──────────────────────────────────────
            planned_paths: list[str] = []
            for object_id, writes in groups.items():
                path = self._get_path(writes)
                if path is None:
                    continue
                planned_paths.append(path)
                if self.fm.exists(path):
                    pre_hashes[path] = hash_file(path)

            for object_id, writes in groups.items():
                path = self._get_path(writes)
                if path is None:
                    continue

                content = self._get_content(writes)
                operations = self._get_operations(writes)

                # ── 逐文件写前再验（堵住文件之间的窗口）────────
                if path in pre_hashes:
                    cur = hash_file(path)
                    if cur != pre_hashes[path]:
                        uncertain = self._rollback_applied(applied)
                        return ProjectionResult(
                            name=self.name,
                            success=False,
                            reason=(
                                f"external change during batch write detected on {path} "
                                f"(hash drifted before write); rolled back {len(applied)} file(s)"
                                + (
                                    f"; uncertain (rollback failed): {uncertain}"
                                    if uncertain
                                    else ""
                                )
                            ),
                            retryable=True,
                            uncertain_paths=uncertain,
                        )

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
                    uncertain = self._rollback_applied(applied)
                    return ProjectionResult(
                        name=self.name,
                        success=False,
                        reason=(
                            f"projection_failed: syntax validation failed for {path}: {msg}"
                            + (
                                f"; uncertain (rollback failed): {uncertain}"
                                if uncertain
                                else ""
                            )
                        ),
                        retryable=True,
                        uncertain_paths=uncertain,
                    )

            for object_id in delta.objects_deleted:
                path = self._path_for_object(object_id, delta)
                if path and self.fm.exists(path):
                    try:
                        self.backup.backup(path)
                    except Exception:
                        pass
                    os.remove(path)
                    deleted.append(path)

            # 磁盘已实际同步到 receipt.version：现在才推进 disk_synced_version。
            if self.sync_state is not None:
                try:
                    self.sync_state.mark_disk_synced(
                        version=receipt.version,
                        written_paths=applied,
                        deleted_paths=deleted,
                        source=getattr(receipt, "source", "forge_tool"),
                    )
                except Exception:
                    import sys
                    print(
                        f"[sync] mark_disk_synced failed after write (version={receipt.version})",
                        file=sys.stderr,
                    )

            return ProjectionResult(name=self.name, success=True)
        except Exception as e:
            uncertain = self._rollback_applied(applied)
            return ProjectionResult(
                name=self.name,
                success=False,
                reason=str(e)
                + (
                    f"; uncertain (rollback failed): {uncertain}"
                    if uncertain
                    else ""
                ),
                retryable=True,
                uncertain_paths=uncertain,
            )

    def _rollback_applied(self, applied: list[str]) -> list[str]:
        """尽力回滚本批已写入的文件；返回回滚失败、状态未知的路径。

        回滚失败的路径会从 sync_state.last_known_file_hashes 中移除，
        避免下次 detect 把不确定内容当成已知基线。
        """
        uncertain: list[str] = []
        for p in applied:
            ok = False
            try:
                ok = bool(self.backup.restore_latest(p))
            except Exception:
                ok = False
            if not ok:
                uncertain.append(p)
        if uncertain and self.sync_state is not None:
            try:
                self.sync_state.forget_paths(uncertain)
            except Exception:
                pass
        return uncertain

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

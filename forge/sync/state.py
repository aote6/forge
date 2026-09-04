"""SyncState — Forge Sync 层的持久化同步水位与已知状态。

落点：`<project_root>/.forge/sync_state.json`（决策 1：不放入 Veritas）。

这是同步关系的权威状态；不得再建立另一套互相独立的同步真相。

记录内容（契约 §5）：
- `disk_synced_version`: 磁盘已**实际**同步到的 World version 水位
- `last_known_commit`: 上一次同步时观察到的 Git HEAD commit
- `last_known_file_hashes`: 上一次同步时 Forge 管理文件的 sha256（绝对路径 key）
- `last_sync`: 最近一次同步事实（含 source: forge_tool / external_sync）

规则 A（不可违反）：只有磁盘真正完成同步后 `disk_synced_version` 才可推进。
任何 skip / conflict / 部分失败都不得推进此水位。
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from forge.sync.git_utils import git_head_commit, hash_file


class SyncState:
    """持久化 World ↔ Disk/Git 同步水位。"""

    def __init__(self, project_root: str | Path):
        self.project_root = str(Path(project_root).expanduser().resolve())
        self._dir = Path(self.project_root) / ".forge"
        self._dir.mkdir(parents=True, exist_ok=True)
        self._file = self._dir / "sync_state.json"

        self._disk_synced_version: int = 0
        self._last_known_commit: str = ""
        self._last_known_file_hashes: dict[str, str] = {}
        self._last_sync: dict | None = None
        self._load()

    # ── persistence ────────────────────────────────────────────

    def _load(self) -> None:
        try:
            if self._file.exists():
                data = json.loads(self._file.read_text(encoding="utf-8"))
                self._disk_synced_version = int(data.get("disk_synced_version", 0))
                self._last_known_commit = str(data.get("last_known_commit", "") or "")
                self._last_known_file_hashes = dict(
                    data.get("last_known_file_hashes", {}) or {}
                )
                self._last_sync = data.get("last_sync")
        except Exception as e:
            import sys

            print(f"[sync] load failed: {e}, starting fresh", file=sys.stderr)
            self._disk_synced_version = 0
            self._last_known_commit = ""
            self._last_known_file_hashes = {}
            self._last_sync = None
            try:
                broken = self._file.with_suffix(".json.broken")
                self._file.rename(broken)
            except Exception as e:
                print(f"[sync] rename to .broken failed: {e}", file=sys.stderr)
                pass

    def _save(self) -> None:
        tmp = self._file.with_suffix(".tmp")
        data = {
            "disk_synced_version": self._disk_synced_version,
            "last_known_commit": self._last_known_commit,
            "last_known_file_hashes": self._last_known_file_hashes,
            "last_sync": self._last_sync,
        }
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self._file)

    # ── read accessors ─────────────────────────────────────────

    @property
    def disk_synced_version(self) -> int:
        return self._disk_synced_version

    @property
    def last_known_commit(self) -> str:
        return self._last_known_commit

    @property
    def last_known_file_hashes(self) -> dict[str, str]:
        return dict(self._last_known_file_hashes)

    @property
    def last_sync(self) -> dict | None:
        return dict(self._last_sync) if self._last_sync else None

    # ── mutation ───────────────────────────────────────────────

    def mark_disk_synced(
        self,
        version: int,
        written_paths: list[str],
        deleted_paths: list[str],
        source: str = "forge_tool",
    ) -> None:
        """磁盘已实际物化到 `version`：推进 disk_synced_version 并更新已知 hash。

        仅在 FileProjection 完整写盘成功后调用（规则 A）。
        """
        for path in written_paths:
            digest = hash_file(path)
            if digest is not None:
                self._last_known_file_hashes[path] = digest
        for path in deleted_paths:
            self._last_known_file_hashes.pop(path, None)

        if version > self._disk_synced_version:
            self._disk_synced_version = version
        self._last_known_commit = git_head_commit(self.project_root)
        self._last_sync = {
            "source": source,
            "version": version,
            "at": time.time(),
        }
        self._save()

    def record_external_sync(
        self,
        commit: str | None = None,
        recompute_hashes: bool = True,
    ) -> None:
        """记录一次外部同步事实（source=external_sync），不推进 World version。

        Disk / Git 是内容权威；外部同步不伪造 World transaction（契约 §6）。
        只更新 sync metadata 中的 last_known_commit / file hashes。
        """
        if commit is None:
            commit = git_head_commit(self.project_root)
        self._last_known_commit = commit or self._last_known_commit
        if recompute_hashes:
            for path in list(self._last_known_file_hashes.keys()):
                digest = hash_file(path)
                if digest is None:
                    self._last_known_file_hashes.pop(path, None)
                else:
                    self._last_known_file_hashes[path] = digest
        self._last_sync = {
            "source": "external_sync",
            "commit": self._last_known_commit,
            "at": time.time(),
        }
        self._save()

    def accept_disk_wins(
        self,
        authorized_world_version: int,
        *,
        source: str = "user_reconcile_disk_wins",
        recompute_hashes: bool = True,
    ) -> None:
        """用户授权 disk_to_world：一次 durable mutation 完成 baseline + watermark supersede。

        - 仅刷新已有 last_known_file_hashes（缺失 drop；不纳入 untracked）
        - 更新 last_known_commit
        - last_sync.source 默认为 user_reconcile_disk_wins
        - disk_synced_version := authorized_world_version
        - 不变量：最终 watermark <= authorized_world_version（赋值即为授权上界）
        - 若当前 watermark 已 > authorized：拒绝写入（应在 preflight 阶段已拦截）
        """
        try:
            authorized = int(authorized_world_version)
        except (TypeError, ValueError) as e:
            raise ValueError(
                f"authorized_world_version must be int, got {authorized_world_version!r}"
            ) from e
        if authorized < 0:
            raise ValueError(f"authorized_world_version must be >= 0, got {authorized}")
        if self._disk_synced_version > authorized:
            raise ValueError(
                f"refuse accept_disk_wins: current disk_synced_version="
                f"{self._disk_synced_version} > authorized={authorized}"
            )

        if recompute_hashes:
            for path in list(self._last_known_file_hashes.keys()):
                digest = hash_file(path)
                if digest is None:
                    self._last_known_file_hashes.pop(path, None)
                else:
                    self._last_known_file_hashes[path] = digest

        commit = git_head_commit(self.project_root)
        self._last_known_commit = commit or self._last_known_commit
        # watermark 精确设为授权上界；永不超过 authorized
        self._disk_synced_version = authorized
        self._last_sync = {
            "source": source or "user_reconcile_disk_wins",
            "version": authorized,
            "commit": self._last_known_commit,
            "at": time.time(),
        }
        self._save()

    def forget_paths(self, paths: list[str]) -> None:
        """从已知集合中移除路径（回滚失败 / 磁盘状态不确定时使用）。

        这些路径不得再被 detect() 当成“已知且可信”的基线；
        下次同步必须重新对账或由用户人工确认。
        不推进、也不回退 disk_synced_version。
        """
        changed = False
        for path in paths:
            if path in self._last_known_file_hashes:
                del self._last_known_file_hashes[path]
                changed = True
        if changed:
            self._save()

    def reset(self) -> None:
        """测试/重建用：清空水位。"""
        self._disk_synced_version = 0
        self._last_known_commit = ""
        self._last_known_file_hashes = {}
        self._last_sync = None
        self._save()

    def to_dict(self) -> dict:
        return {
            "disk_synced_version": self._disk_synced_version,
            "last_known_commit": self._last_known_commit,
            "last_known_file_hashes": dict(self._last_known_file_hashes),
            "last_sync": dict(self._last_sync) if self._last_sync else None,
        }

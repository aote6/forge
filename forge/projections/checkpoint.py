"""ProjectionCheckpoint — 持久化投影消费进度，实现进程重启后幂等恢复。

特性：
- 基于 Veritas Receipt.version（global_version，单调递增）
- O(1) 空间，per-projection checkpoint
- 原子写入（tmp + fsync + rename），断电不丢数据
- 写入失败抛 RuntimeError，不静默丢弃
"""

from __future__ import annotations

import json
import os
from pathlib import Path


class ProjectionCheckpoint:
    """持久化投影进度。"""

    def __init__(self, store_dir: str = ".forge"):
        self._dir = Path(store_dir)
        self._dir.mkdir(parents=True, exist_ok=True)
        self._file = self._dir / "projection_checkpoint.json"
        self._checkpoints: dict[str, int] = {}
        self._tx_ids: dict[str, list] = {}  # persisted as list for JSON
        self._load()

    def _load(self) -> None:
        try:
            if self._file.exists():
                with open(self._file) as f:
                    data = json.load(f)
                    self._checkpoints = data.get("versions", {})
                    self._tx_ids = data.get("tx_ids", {})
        except Exception:
            self._checkpoints = {}
            self._tx_ids = {}

    def _save(self) -> None:
        """原子写入：tmp → fsync → rename。失败抛异常。"""
        tmp = self._file.with_suffix(".tmp")
        try:
            with open(tmp, "w") as f:
                data = {
                    "versions": self._checkpoints,
                    "tx_ids": {k: list(v) for k, v in self._tx_ids.items()},
                }
                json.dump(data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self._file)
        except Exception:
            raise RuntimeError(
                f"ProjectionCheckpoint save failed: {self._file}. "
                "Projection 已应用但 checkpoint 未持久化，重启可能重复投影。"
            )

    def should_apply(self, projection_name: str, receipt_version: int) -> bool:
        last = self._checkpoints.get(projection_name, 0)
        return receipt_version > last

    def mark_applied(self, projection_name: str, receipt_version: int) -> None:
        current = self._checkpoints.get(projection_name, 0)
        if receipt_version > current:
            self._checkpoints[projection_name] = receipt_version
            self._save()

    def get_last_applied_tx_id(self, projection_name: str) -> int:
        """返回此投影最后应用的 tx_id（从 persisted tx_ids 恢复）。"""
        return self._tx_ids.get(projection_name, 0)

    def mark_tx_applied(self, projection_name: str, tx_id: int) -> None:
        if projection_name not in self._tx_ids:
            self._tx_ids[projection_name] = set()
        self._tx_ids[projection_name].add(tx_id)

    @property
    def checkpoints(self) -> dict[str, int]:
        return dict(self._checkpoints)

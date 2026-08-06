"""ProjectionCheckpoint — 持久化投影消费进度，实现进程重启后幂等恢复。

利用 Veritas Receipt.version（global_version，单调递增）做 checkpoint：
- 启动时加载 last_applied_version
- receipt.version <= last_applied_version → 跳过
- receipt.version > last_applied_version → apply，更新 checkpoint

O(1) 空间，不存储无限 tx_id 集合。
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
        self._checkpoints: dict[str, int] = {}  # projection_name → last_applied_version
        self._load()

    def _load(self) -> None:
        try:
            if self._file.exists():
                with open(self._file) as f:
                    self._checkpoints = json.load(f)
        except Exception:
            self._checkpoints = {}

    def _save(self) -> None:
        try:
            with open(self._file, "w") as f:
                json.dump(self._checkpoints, f, indent=2)
        except Exception:
            pass

    def should_apply(self, projection_name: str, receipt_version: int) -> bool:
        """检查此 receipt 是否已被该投影消费。"""
        last = self._checkpoints.get(projection_name, 0)
        return receipt_version > last

    def mark_applied(self, projection_name: str, receipt_version: int) -> None:
        current = self._checkpoints.get(projection_name, 0)
        if receipt_version > current:
            self._checkpoints[projection_name] = receipt_version
            self._save()

    @property
    def checkpoints(self) -> dict[str, int]:
        return dict(self._checkpoints)

"""Forge Sync Layer — World ↔ Disk/Git 双向同步边界与一致性语义。

契约 docs/WORLD_DISK_SYNC.md。
"""

from forge.sync.state import SyncState
from forge.sync.sync_layer import (
    CONFLICT,
    FAST_FORWARD_DISK_TO_WORLD,
    FAST_FORWARD_WORLD_TO_DISK,
    IN_SYNC,
    NOT_A_GIT_REPO,
    SyncLayer,
    SyncReport,
)

__all__ = [
    "SyncState",
    "SyncLayer",
    "SyncReport",
    "IN_SYNC",
    "FAST_FORWARD_DISK_TO_WORLD",
    "FAST_FORWARD_WORLD_TO_DISK",
    "CONFLICT",
    "NOT_A_GIT_REPO",
]

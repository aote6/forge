"""RecoveryCheck — 启动时同步状态检测（只读，不写磁盘）。

契约 §1 / §3 / §4：Recovery 不得把 World receipt 当作覆盖磁盘的授权；
启动时只做同步状态检测，发现无法安全对齐则 STOP / CONFLICT。
任何"从 World 恢复缺失文件"的特殊逻辑在此禁止。
"""

from __future__ import annotations

from forge.sync.sync_layer import SyncLayer, SyncReport


class RecoveryCheck:
    """启动恢复检查。仅调用 SyncLayer.detect()，绝不重放 receipt 写磁盘。"""

    def __init__(self, sync_layer: SyncLayer):
        self._sync_layer = sync_layer

    def check(self) -> SyncReport:
        """返回同步状态报告；不产生任何磁盘副作用。"""
        return self._sync_layer.detect()

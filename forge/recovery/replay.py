"""ProjectionRecovery — 启动时从 Veritas 重放未消费的历史 receipt。

流程：
1. 读取各 Projection 的 checkpoint（last_applied_version）
2. 从 Veritas 获取 receipts_since(last_version)
3. 按 version 顺序逐条 project
4. 每条成功后自动更新 checkpoint
"""

from __future__ import annotations


class ProjectionRecovery:
    """启动恢复引擎。"""

    def __init__(self, world_runtime, projection_manager):
        self._world = world_runtime
        self._pm = projection_manager

    def recover(self) -> dict[str, int]:
        """执行恢复，返回每个 Projection 恢复的 receipt 数量。"""
        recovered = {}

        for proj in self._pm._projections:
            name = proj.name
            last_version = self._pm._checkpoint.checkpoints.get(name, 0)
            receipts = self._world.get_receipts_since(last_version)
            count = 0
            for receipt in receipts:
                self._pm.project(receipt, receipt.delta)
                count += 1
            recovered[name] = count

        return recovered

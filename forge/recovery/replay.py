"""ProjectionRecovery — 启动时从 Veritas 重放未消费的历史 receipt。

流程：
1. 读取各 Projection 的 checkpoint（last_applied_version）
2. 从 Veritas 获取 receipts_since(last_version)
3. 按 version 顺序逐条 project
4. 每条成功后自动更新 checkpoint

分叉策略（FileProjection）：
- recovery 期间开启 recovery_preserve_disk
- 若磁盘文件已存在且内容与 World 投影结果不同，跳过覆盖（保留用户手动修改）
- 仍推进 checkpoint，避免每次启动反复重放同一批 receipt
- 缺失的文件仍会从 World 恢复（满足崩溃恢复）
"""

from __future__ import annotations

import sys


class ProjectionRecovery:
    """启动恢复引擎。"""

    def __init__(self, world_runtime, projection_manager):
        self._world = world_runtime
        self._pm = projection_manager

    def _set_preserve_disk(self, enabled: bool) -> None:
        for proj in self._pm.projections:
            if hasattr(proj, "recovery_preserve_disk"):
                proj.recovery_preserve_disk = enabled

    def recover(self) -> dict[str, int]:
        """执行恢复，返回每个 Projection 恢复的 receipt 数量。"""
        recovered: dict[str, int] = {}
        skipped_paths: list[str] = []

        # 保护磁盘上手动修改：与 World 分叉时不覆盖
        self._set_preserve_disk(True)
        try:
            for proj in self._pm.projections:
                name = proj.name
                last_version = self._pm._checkpoint.checkpoints.get(name, 0)
                receipts = self._world.get_receipts_since(last_version)
                receipts.sort(key=lambda r: r.version)  # 确保按 version 升序
                count = 0
                for receipt in receipts:
                    results = self._pm.project(receipt, receipt.delta)
                    if all(r.success for r in results):
                        count += 1
                    else:
                        failed = [r.name for r in results if not r.success]
                        print(
                            f"[recovery] {name}: skipped v{receipt.version} — {failed} failed",
                            file=sys.stderr,
                        )
                    # 收集 FileProjection 跳过的分叉路径
                    for p in self._pm.projections:
                        skipped = getattr(p, "last_skipped_diverged", None) or []
                        for path in skipped:
                            if path not in skipped_paths:
                                skipped_paths.append(path)
                recovered[name] = count
        finally:
            self._set_preserve_disk(False)

        if skipped_paths:
            print(
                "[recovery] disk/World 分叉，已保留磁盘版本（未覆盖）:",
                file=sys.stderr,
            )
            for path in skipped_paths[:20]:
                print(f"  - {path}", file=sys.stderr)
            if len(skipped_paths) > 20:
                print(f"  ... 共 {len(skipped_paths)} 个文件", file=sys.stderr)
            print(
                "[recovery] 若要以磁盘为准同步 World，请用工具重新写入这些文件；"
                "若要以 World 为准，删除对应文件后重启以触发恢复。",
                file=sys.stderr,
            )

        return recovered

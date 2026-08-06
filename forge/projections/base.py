"""Projection 基类 — 所有投影的抽象接口。

Projection 不参与事务决策。Projection 只消费已提交的事务。
Projection 永远不是世界状态。Projection 永远可以删除并重建。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

from forge.world.types import Receipt, TransactionDelta


@dataclass
class ProjectionResult:
    """一次 Projection.apply 的结果。"""
    name: str
    success: bool
    reason: str = ""
    retryable: bool = False


class Projection(ABC):
    """投影基类。

    生命周期分两个阶段：
    1. prepare(delta) — 只读预览，不能修改外部世界
    2. apply(receipt, delta) — 事务已提交，执行实际投影更新
    """

    @property
    @abstractmethod
    def name(self) -> str:
        ...

    @abstractmethod
    def prepare(self, delta: TransactionDelta) -> Optional[dict]:
        """准备阶段。返回用户确认所需的信息（如 diff）。此阶段不能修改外部世界。"""
        ...

    @abstractmethod
    def apply(self, receipt: Receipt, delta: TransactionDelta) -> ProjectionResult:
        """应用阶段。事务已提交，执行实际更新。失败不回滚世界事务。"""
        ...

    def idempotency_key(self, receipt: Receipt) -> int:
        """返回此 receipt 的幂等键。默认用 tx_id。子类可重写。"""
        return receipt.tx_id


class ProjectionManager:
    """管理所有 Projection 的生命周期和分发。"""

    def __init__(self):
        self._projections: list[Projection] = []
        from forge.projections.applied_store import AppliedTransactionStore
        self._applied = AppliedTransactionStore()
        from forge.projections.checkpoint import ProjectionCheckpoint
        self._checkpoint = ProjectionCheckpoint()
        self._applied_tx: dict[str, set] = {}  # projection_name -> {tx_id}
        # 从 checkpoint 恢复已应用的 tx_id
        for name, tx_ids in self._checkpoint._tx_ids.items():
            self._applied_tx[name] = set(tx_ids)

    def register(self, projection: Projection) -> None:
        self._projections.append(projection)

    def unregister(self, projection: Projection) -> None:
        self._projections.remove(projection)

    @property
    def projections(self) -> list:
        return list(self._projections)

    @property
    def projection_names(self) -> list:
        return [p.name for p in self._projections]

    @property
    def checkpoint(self):
        return self._checkpoint

    def prepare_all(self, delta: TransactionDelta) -> dict[str, dict]:
        confirmations = {}
        for p in self._projections:
            result = p.prepare(delta)
            if result is not None:
                confirmations[p.name] = result
        return confirmations

    def project(self, receipt: Receipt, delta: TransactionDelta) -> list[ProjectionResult]:
        """运行所有 Projection 的 apply，统一收集结果。双层幂等保护。"""
        # 进程级幂等：tx_id 去重
        if not self._applied.should_apply(receipt):
            return [ProjectionResult(name="_manager", success=True, reason="skipped: already applied")]
        results: list[ProjectionResult] = []
        for p in self._projections:
            # 1. 持久化幂等：version 检查
            if not self._checkpoint.should_apply(p.name, receipt.version):
                results.append(ProjectionResult(
                    name=p.name, success=True, reason="skipped: version <= checkpoint"
                ))
                continue

            # 2. Projection 级幂等：tx_id 去重
            if p.name not in self._applied_tx:
                self._applied_tx[p.name] = set()
            tx_key = p.idempotency_key(receipt)
            if tx_key in self._applied_tx[p.name]:
                results.append(ProjectionResult(
                    name=p.name, success=True, reason="skipped: tx already applied"
                ))
                continue

            try:
                result = p.apply(receipt, delta)
                if result.success:
                    self._applied_tx[p.name].add(tx_key)
                    self._checkpoint.mark_applied(p.name, receipt.version)
                results.append(result)
            except Exception as e:
                results.append(ProjectionResult(
                    name=p.name, success=False, reason=str(e), retryable=True
                ))
        self._applied.mark_applied(receipt)
        return results
        for p in self._projections:
            try:
                result = p.apply(receipt, delta)
                results.append(result)
            except Exception as e:
                results.append(ProjectionResult(
                    name=p.name,
                    success=False,
                    reason=f"{type(e).__name__}: {e}",
                    retryable=True,
                ))
        return results

    # back-compat alias
    def apply_all(self, receipt: Receipt, delta: TransactionDelta) -> list[ProjectionResult]:
        return self.project(receipt, delta)

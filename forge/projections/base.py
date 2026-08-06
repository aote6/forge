"""Projection 基类 — 所有投影的抽象接口。

Projection 不参与事务决策。Projection 只消费已提交的事务。
Projection 永远不是世界状态。Projection 永远可以删除并重建。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional

from forge.world.types import Receipt, TransactionDelta



class Projection(ABC):
    """投影基类。

    生命周期分两个阶段：
    1. prepare(delta) — 只读预览，不能修改外部世界
    2. apply(receipt, delta) — 事务已提交，执行实际投影更新
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """投影名称，用于注册和日志。"""
        ...

    @abstractmethod
    def prepare(self, delta: TransactionDelta) -> Optional[dict]:
        """准备阶段。返回用户确认所需的信息（如 diff）。

        此阶段不能修改外部世界。
        返回 None 表示不需要用户确认。
        返回 dict 表示需要确认，内容会展示给用户。
        """
        ...

    @abstractmethod
    def apply(self, receipt: Receipt, delta: TransactionDelta) -> None:
        """应用阶段。事务已提交，执行实际更新。

        此方法失败不会回滚世界事务。
        Projection 可以稍后恢复。
        """
        ...


class ProjectionManager:
    """管理所有 Projection 的生命周期和分发。"""

    def __init__(self):
        self._projections: list[Projection] = []

    def register(self, projection: Projection) -> None:
        self._projections.append(projection)

    def unregister(self, projection: Projection) -> None:
        self._projections.remove(projection)

    def prepare_all(self, delta: TransactionDelta) -> dict[str, dict]:
        """运行所有 Projection 的 prepare 阶段，收集确认信息。"""
        confirmations = {}
        for p in self._projections:
            result = p.prepare(delta)
            if result is not None:
                confirmations[p.name] = result
        return confirmations

    def apply_all(self, receipt: Receipt, delta: TransactionDelta) -> None:
        """运行所有 Projection 的 apply 阶段。"""
        for p in self._projections:
            p.apply(receipt, delta)

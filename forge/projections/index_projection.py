"""IndexProjection — 代码索引投影。

当前为懒索引模式，apply 为 no-op。
"""

from forge.projections.base import Projection, ProjectionResult, TransactionDelta
from forge.world.types import Receipt


class IndexProjection(Projection):
    """代码索引投影。"""

    def __init__(self, project_root: str = "."):
        self.project_root = project_root

    @property
    def name(self) -> str:
        return "index"

    def prepare(self, delta: TransactionDelta) -> dict | None:
        return None

    def apply(self, receipt: Receipt, delta: TransactionDelta) -> ProjectionResult:
        return ProjectionResult(name=self.name, success=True, reason="lazy index, no-op")

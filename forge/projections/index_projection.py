"""IndexProjection — 代码索引投影。

当前 AutoIndexer 是懒索引模式（搜索时才执行 ripgrep/grep），
因此 IndexProjection 不需要主动重建索引，仅作为占位。
未来可扩展为主动索引（如 ElasticSearch、SQLite FTS）。
"""

from forge.projections.base import Projection, TransactionDelta
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

    def apply(self, receipt: Receipt, delta: TransactionDelta) -> None:
        pass  # 懒索引模式，无需主动重建

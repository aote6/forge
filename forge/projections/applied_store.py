"""AppliedTransactionStore — Projection 幂等保护。

使用 tx_id 集合做幂等检查。
不依赖 root_hash 大小比较（root_hash 是 FNV-1a，非单调版本号）。
"""


class AppliedTransactionStore:
    """追踪已应用的事务，保证 Projection 幂等。"""

    def __init__(self):
        self._applied_tx_ids: set[int] = set()

    def should_apply(self, receipt) -> bool:
        """tx_id 已存在则跳过。"""
        return receipt.tx_id not in self._applied_tx_ids

    def mark_applied(self, receipt) -> None:
        self._applied_tx_ids.add(receipt.tx_id)

    @property
    def last_tx_id(self) -> int:
        return max(self._applied_tx_ids) if self._applied_tx_ids else 0

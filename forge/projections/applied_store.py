"""AppliedTransactionStore — Projection 幂等保护。

使用 Veritas Receipt 的 after_root 做 checkpoint：
- 如果 receipt.after_root <= 已应用的 last_root，则跳过
- 天然幂等，不依赖外部持久化
"""


class AppliedTransactionStore:
    """追踪已应用的事务，保证 Projection 幂等。"""

    def __init__(self):
        self._last_root: int = 0
        self._applied_tx_ids: set[int] = set()

    def should_apply(self, receipt) -> bool:
        """检查此 receipt 是否已经应用过。

        使用 after_root 单调递增特性：
        - 如果 after_root <= last_root，说明已经应用过
        - 如果 tx_id 已经在集合中，说明重复
        """
        if receipt.tx_id in self._applied_tx_ids:
            return False
        if receipt.after_root <= self._last_root:
            return False
        return True

    def mark_applied(self, receipt) -> None:
        self._last_root = max(self._last_root, receipt.after_root)
        self._applied_tx_ids.add(receipt.tx_id)

    @property
    def last_root(self) -> int:
        return self._last_root

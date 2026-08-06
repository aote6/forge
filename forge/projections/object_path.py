"""Object Path Mapping — 从 TransactionDelta 提取 ObjectId → 文件路径的映射。

路径来源：memory_written 中 state_id=0 的 value_hex。
TODO: 这是临时协议。state_id=0 表示 path 是 Forge 自定义约定，
不是 Veritas Object 的标准 metadata。
未来应由 Veritas Object 携带正式的 path/type/owner metadata，
通过 Object metadata 查询而非解析 memory slot。
"""

from __future__ import annotations


class ObjectPathMap:
    """从 delta 中提取并缓存 object_id → 文件路径映射。"""

    def __init__(self):
        self._paths: dict[int, str] = {}

    def update_from_delta(self, delta) -> None:
        """从 TransactionDelta.memory_written 提取路径（state_id=0）。"""
        for w in delta.memory_written:
            oid = w.get("object_id") if isinstance(w, dict) else w[0]
            sid = w.get("state_id") if isinstance(w, dict) else w[1]
            if sid != 0:
                continue
            val = w.get("value_hex") if isinstance(w, dict) else w[2] if len(w) > 2 else ""
            if not val:
                continue
            try:
                path = bytes.fromhex(val).decode("utf-8")
            except Exception:
                path = val
            self._paths[oid] = path

    def get(self, object_id: int) -> str | None:
        return self._paths.get(object_id)

    def remove(self, object_id: int) -> None:
        self._paths.pop(object_id, None)

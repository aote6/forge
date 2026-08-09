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
        self._caps: dict[int, int] = {}  # object_id -> its own AdminCap capability_id

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

        # Self-admin-cap pattern: grantee == resource means the object holds
        # the AdminCap over itself (the case for freshly created objects).
        # Rebuilt on every replay from Veritas' authoritative capability_grants,
        # so this stays valid across process restarts — no separate file needed.
        for g in getattr(delta, "capability_grants", None) or []:
            if g.grantee == g.resource:
                self._caps[g.resource] = g.capability_id

    def get(self, object_id: int) -> str | None:
        return self._paths.get(object_id)

    def set(self, object_id: int, path: str) -> None:
        self._paths[object_id] = path

    def find_object_id(self, path: str) -> int | None:
        """Reverse lookup: file path → object_id."""
        for oid, p in self._paths.items():
            if p == path or str(p) == path:
                return oid
        return None

    def get_from_metadata(self, object_id: int) -> str | None:
        """TODO: 未来从 Veritas Object metadata 查询路径。
        当前回退到 state_id=0 的临时方案。
        当 Veritas Object 携带 path metadata 时，此方法应直接查询 metadata。
        """
        return self._paths.get(object_id)

    def remove(self, object_id: int) -> None:
        self._paths.pop(object_id, None)
        self._caps.pop(object_id, None)

    def get_capability_id(self, object_id: int) -> int | None:
        """Return the AdminCap capability_id this object holds over itself, if any."""
        return self._caps.get(object_id)

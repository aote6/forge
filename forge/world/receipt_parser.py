"""Receipt parser — 唯一的 JSON → Receipt 解析入口。

WorldAdapter 和测试都使用此函数，避免解析逻辑漂移。
"""

from forge.world.types import Receipt, TransactionDelta, CapabilityGrantView


def _as_root(value) -> int:
    """Accept int or hex/decimal string roots from veritasd."""
    if value is None:
        return 0
    if isinstance(value, int):
        return value
    s = str(value).strip()
    if not s:
        return 0
    try:
        return int(s, 10)
    except ValueError:
        return int(s, 16)


def parse_receipt(resp: dict) -> Receipt:
    """从 veritasd 响应解析 Receipt（含权威 TransactionDelta）。

    这是 Forge 中唯一允许构造 TransactionDelta 的位置。
    """
    r = resp.get("receipt") or {}
    d = r.get("delta") or {}

    delta = TransactionDelta(
        actor_id=int(d.get("actor_id", 0)),
        objects_created=[int(x) for x in d.get("objects_created", [])],
        objects_deleted=[int(x) for x in d.get("objects_deleted", [])],
        objects_frozen=[int(x) for x in d.get("objects_frozen", [])],
        links_added=[(int(f), int(t), lt) for f, t, lt in d.get("links_added", [])],
        links_removed=[(int(f), int(t)) for f, t in d.get("links_removed", [])],
        memory_written=d.get("memory_written", []),
        capability_events=d.get("capability_events", []),
        capability_grants=[
            CapabilityGrantView(
                capability_id=int(g["capability_id"]),
                cap_type=g["cap_type"],
                grantor=int(g["grantor"]),
                grantee=int(g["grantee"]),
                resource=int(g["resource"]),
            )
            for g in d.get("capability_grants", [])
        ],
        effects=[(k, v) for k, v in d.get("effects", [])],
    )

    return Receipt(
        tx_id=int(r.get("tx_id", 0)),
        before_root=_as_root(r.get("before_root", 0)),
        after_root=_as_root(r.get("after_root", 0)),
        version=int(r.get("version", 0)),
        delta=delta,
    )

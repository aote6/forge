"""World-facing data types (JSON contract with veritasd)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ObjectInfo:
    object_id: int
    state: str


@dataclass
class LinkInfo:
    from_id: int
    to_id: int
    link_type: str


@dataclass
class MemoryWriteView:
    object_id: int
    state_id: int
    value_hex: str


@dataclass
class CapabilityGrantView:
    capability_id: int
    cap_type: str
    grantor: int
    grantee: int
    resource: int

@dataclass
class Receipt:
    tx_id: int
    before_root: int
    after_root: int
    version: int
    delta: TransactionDelta = field(default_factory=lambda: TransactionDelta())
    # Forge-side annotation（契约 §6）：区分事实来源。
    # - forge_tool: 由 Forge 工具经 Veritas 正常提交产生
    # - external_sync: 用户进行外部同步这一事实（不伪造为 Forge 修改）
    source: str = "forge_tool"


@dataclass
class WorldInfo:
    version: int
    state_root: int
    object_count: int


@dataclass
class TransactionDelta:
    """描述一次已提交事务中发生了什么变化。
    权威来源为 Veritas Machine 的 commit 响应。
    """
    actor_id: int = 0
    objects_created: list[int] = field(default_factory=list)
    objects_deleted: list[int] = field(default_factory=list)
    objects_frozen: list[int] = field(default_factory=list)
    links_added: list[tuple] = field(default_factory=list)
    links_removed: list[tuple] = field(default_factory=list)
    memory_written: list = field(default_factory=list)  # list of MemoryWriteView-like dicts
    capability_events: list[str] = field(default_factory=list)
    capability_grants: list = field(default_factory=list)  # list[CapabilityGrantView]
    effects: list[tuple] = field(default_factory=list)
    # Forge-side annotation (e.g. deleted_paths: {object_id: path}).
    # Not part of Veritas canonical delta; attached after commit for Projection.
    metadata: dict = field(default_factory=dict)

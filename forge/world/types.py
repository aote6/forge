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
class Receipt:
    tx_id: int
    before_root: int
    after_root: int
    version: int


@dataclass
class WorldInfo:
    version: int
    state_root: int
    object_count: int


@dataclass
class TransactionDelta:
    """描述一次已提交事务中发生了什么变化。

    权威来源应为 Veritas Machine。
    当前 veritasd 尚未在 commit 响应中返回 delta 时，
    由 WorldSession 提供 Temporary Stub（仅作投影输入，不作为世界状态源）。
    """
    objects_created: list[int] = field(default_factory=list)
    objects_deleted: list[int] = field(default_factory=list)
    objects_frozen: list[int] = field(default_factory=list)
    links_added: list[tuple] = field(default_factory=list)
    links_removed: list[tuple] = field(default_factory=list)
    memory_written: dict = field(default_factory=dict)
    metadata: dict = field(default_factory=dict)

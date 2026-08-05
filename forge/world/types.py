"""World-facing data types (JSON contract with veritasd)."""

from __future__ import annotations

from dataclasses import dataclass
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

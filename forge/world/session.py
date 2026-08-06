"""WorldSession — long-lived Veritas transaction held by Forge."""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from forge.world.types import Receipt
from forge.world.types import TransactionDelta

if TYPE_CHECKING:
    from forge.world.adapter import WorldAdapter


class SessionClosedError(RuntimeError):
    pass


class WorldSession:
    """One session == one Kernel TransactionContext on the veritasd side."""

    def __init__(self, adapter: "WorldAdapter", session_id: int, actor_id: Optional[int]):
        self._adapter = adapter
        self.session_id = session_id
        self.actor_id = actor_id
        self._closed = False
        # 追踪本 session 内的所有操作，用于构造 TransactionDelta
        self._created_objects: list[int] = []
        self._deleted_objects: list[int] = []
        self._frozen_objects: list[int] = []
        self._links_added: list[tuple[int, int, str]] = []
        self._links_removed: list[tuple[int, int]] = []
        self._writes: list[tuple[int, int, str]] = []  # (object_id, state_id, value)

    def _ensure_open(self) -> None:
        if self._closed:
            raise SessionClosedError("session already committed or aborted")

    def create_object(self) -> int:
        self._ensure_open()
        oid = self._adapter.tx_create_object(self.session_id)
        self._created_objects.append(oid)
        return oid

    def freeze(self, object_id: int) -> None:
        self._ensure_open()
        self._adapter.tx_freeze_object(self.session_id, object_id)
        self._frozen_objects.append(object_id)

    def death(self, object_id: int) -> None:
        self._ensure_open()
        self._adapter.tx_death_object(self.session_id, object_id)
        self._deleted_objects.append(object_id)

    def link(self, from_id: int, to_id: int, link_type: str = "owns") -> None:
        self._ensure_open()
        self._adapter.tx_link(self.session_id, from_id, to_id, link_type)
        self._links_added.append((from_id, to_id, link_type))

    def unlink(self, from_id: int, to_id: int) -> None:
        self._ensure_open()
        self._adapter.tx_unlink(self.session_id, from_id, to_id)
        self._links_removed.append((from_id, to_id))

    def write(self, object_id: int, state_id: int, value: str = "", hex_value: str | None = None) -> None:
        self._ensure_open()
        self._adapter.tx_write(
            self.session_id, state_id, value=value or None, hex_value=hex_value
        )
        self._writes.append((object_id, state_id, value))

    def commit(self) -> tuple[Receipt, TransactionDelta]:
        self._ensure_open()
        receipt = self._adapter.tx_commit(self.session_id)
        self._closed = True

        # 构造 TransactionDelta（未来 veritasd 直接返回 delta 时替换此处）
        delta = TransactionDelta(
            objects_created=list(self._created_objects),
            objects_deleted=list(self._deleted_objects),
            objects_frozen=list(self._frozen_objects),
            links_added=list(self._links_added),
            links_removed=list(self._links_removed),
            memory_written=self._build_memory_delta(),
        )
        return receipt, delta

    def abort(self) -> None:
        if self._closed:
            return
        self._adapter.tx_abort(self.session_id)
        self._closed = True

    @property
    def closed(self) -> bool:
        return self._closed

    def _build_memory_delta(self) -> dict[int, list[tuple[int, str]]]:
        """将 writes 列表组织为 {object_id: [(state_id, value), ...]}"""
        result: dict[int, list[tuple[int, str]]] = {}
        for obj_id, state_id, value in self._writes:
            if obj_id not in result:
                result[obj_id] = []
            result[obj_id].append((state_id, value))
        return result

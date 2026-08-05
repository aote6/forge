"""WorldSession — long-lived Veritas transaction held by Forge."""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from forge.world.types import Receipt

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

    def _ensure_open(self) -> None:
        if self._closed:
            raise SessionClosedError("session already committed or aborted")

    def create_object(self) -> int:
        self._ensure_open()
        return self._adapter.tx_create_object(self.session_id)

    def freeze(self, object_id: int) -> None:
        self._ensure_open()
        self._adapter.tx_freeze_object(self.session_id, object_id)

    def death(self, object_id: int) -> None:
        self._ensure_open()
        self._adapter.tx_death_object(self.session_id, object_id)

    def link(self, from_id: int, to_id: int, link_type: str = "owns") -> None:
        self._ensure_open()
        self._adapter.tx_link(self.session_id, from_id, to_id, link_type)

    def unlink(self, from_id: int, to_id: int) -> None:
        self._ensure_open()
        self._adapter.tx_unlink(self.session_id, from_id, to_id)

    def write(self, state_id: int, value: str = "", hex_value: str | None = None) -> None:
        self._ensure_open()
        self._adapter.tx_write(
            self.session_id, state_id, value=value or None, hex_value=hex_value
        )

    def commit(self) -> Receipt:
        self._ensure_open()
        receipt = self._adapter.tx_commit(self.session_id)
        self._closed = True
        return receipt

    def abort(self) -> None:
        if self._closed:
            return
        self._adapter.tx_abort(self.session_id)
        self._closed = True

    @property
    def closed(self) -> bool:
        return self._closed

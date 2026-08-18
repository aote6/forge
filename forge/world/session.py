"""WorldSession — long-lived Veritas transaction held by Forge.

commit() 返回 veritasd 的权威 TransactionDelta。
preview_delta() 用本地追踪提供非权威预览，不调用 tx_commit，不产生持久化副作用。
"""

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
        # Local tracking for preview_delta(). Non-authoritative: veritasd is
        # the source of truth once committed. Used only to build an unsubmitted
        # preview of what this session has staged so far.
        self._objects_created: list[int] = []
        self._objects_deleted: list[int] = []
        self._objects_frozen: list[int] = []
        self._links_added: list[tuple] = []
        self._links_removed: list[tuple] = []
        self._memory_written: list[dict] = []

    def _ensure_open(self) -> None:
        if self._closed:
            raise SessionClosedError("session already committed or aborted")

    def create_object(self) -> int:
        self._ensure_open()
        object_id = self._adapter.tx_create_object(self.session_id)
        self._objects_created.append(object_id)
        return object_id

    def freeze(self, object_id: int) -> None:
        self._ensure_open()
        self._adapter.tx_freeze_object(self.session_id, object_id)
        self._objects_frozen.append(object_id)

    def death(self, object_id: int) -> None:
        self._ensure_open()
        self._adapter.tx_death_object(self.session_id, object_id)
        self._objects_deleted.append(object_id)

    def link(self, from_id: int, to_id: int, link_type: str = "owns") -> None:
        self._ensure_open()
        self._adapter.tx_link(self.session_id, from_id, to_id, link_type)
        self._links_added.append((from_id, to_id, link_type))

    def unlink(self, from_id: int, to_id: int) -> None:
        self._ensure_open()
        self._adapter.tx_unlink(self.session_id, from_id, to_id)
        self._links_removed.append((from_id, to_id))

    def grant(
        self,
        grantor: int,
        grantee: int,
        capability_type: str,
        resource: int,
    ) -> None:
        """Thin wrapper: request Veritas to grant capability. No local auth logic."""
        self._ensure_open()
        self._adapter.tx_capability_grant(
            self.session_id, grantor, grantee, capability_type, resource
        )

    def write(self, object_id: int, state_id: int, value: str = "", hex_value: str | None = None) -> None:
        self._ensure_open()
        self._adapter.tx_write(
            self.session_id, state_id, value=value or None, hex_value=hex_value,
            object_id=object_id,
        )
        self._memory_written.append({
            "object_id": object_id,
            "state_id": state_id,
            "value": value,
            "hex_value": hex_value,
        })

    def read(self, object_id: int, state_id: int) -> str:
        """Read object state value. Returns str for LLM consumption."""
        self._ensure_open()
        value = self._adapter.tx_read(self.session_id, state_id, object_id=object_id)
        # Try decode as utf-8, fallback to hex
        try:
            return value.decode("utf-8")
        except (UnicodeDecodeError, AttributeError):
            return value.hex() if isinstance(value, (bytes, bytearray)) else str(value)

    def commit(self) -> tuple[Receipt, TransactionDelta]:
        """提交事务。delta 来自 veritasd，是权威世界状态变化。"""
        self._ensure_open()
        receipt = self._adapter.tx_commit(self.session_id)
        self._closed = True
        return receipt, receipt.delta

    def abort(self) -> None:
        if self._closed:
            return
        self._adapter.tx_abort(self.session_id)
        self._closed = True

    def preview_delta(self) -> TransactionDelta:
        """基于本 session 已发出的操作，在本地拼出一个非权威的 TransactionDelta 预览。

        不调用 tx_commit，不产生持久化副作用。调用方（如 executor.stage()）
        拿到预览后必须 abort() 本 session，避免遗留未提交事务。
        """
        self._ensure_open()
        return TransactionDelta(
            actor_id=self.actor_id or 0,
            objects_created=list(self._objects_created),
            objects_deleted=list(self._objects_deleted),
            objects_frozen=list(self._objects_frozen),
            links_added=list(self._links_added),
            links_removed=list(self._links_removed),
            memory_written=list(self._memory_written),
        )

    @property
    def closed(self) -> bool:
        return self._closed

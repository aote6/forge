"""
WorldRuntime — Forge's presence inside the Veritas world.

LLM / Tools must go through this layer, never through VeritasClient.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from forge.world.adapter import WorldAdapter, WorldAdapterError
from forge.world.identity import IdentityStore
from forge.world.session import WorldSession
from forge.world.types import LinkInfo, ObjectInfo, Receipt, WorldInfo


class WorldRuntime:
    def __init__(
        self,
        project_root: str | Path = ".",
        binary: str = "veritasd",
        adapter: WorldAdapter | None = None,
    ):
        self.project_root = str(Path(project_root).expanduser().resolve())
        self._adapter = adapter or WorldAdapter(self.project_root, binary=binary)
        self._identity = IdentityStore(self.project_root)
        self._object_id: Optional[int] = None
        self._current_session: Optional[WorldSession] = None

    @property
    def adapter(self) -> WorldAdapter:
        return self._adapter

    def connect(self) -> bool:
        return self._adapter.ping()

    def ensure_identity(self) -> int:
        """Restore or create Forge's Object identity in the world."""
        if self._object_id is not None:
            return self._object_id

        stored = self._identity.load()
        if stored is not None:
            info = self._adapter.get_object(stored)
            if info is not None and info.state == "Alive":
                oid = self._adapter.attach_identity(stored)
                self._object_id = oid
                return oid

        oid = self._adapter.attach_identity(None)
        self._identity.save(oid)
        self._object_id = oid
        return oid

    def whoami(self) -> Optional[int]:
        if self._object_id is not None:
            return self._object_id
        return self._adapter.whoami()

    def world_info(self) -> WorldInfo:
        return self._adapter.world_info()

    def list_objects(self) -> list[ObjectInfo]:
        return self._adapter.list_objects()

    def get_object(self, object_id: int) -> Optional[ObjectInfo]:
        return self._adapter.get_object(object_id)

    def get_links(self) -> list[LinkInfo]:
        return self._adapter.get_links()

    def begin_session(self, actor_id: Optional[int] = None) -> WorldSession:
        if self._current_session is not None and not self._current_session.closed:
            try:
                self._current_session.abort()
            except WorldAdapterError:
                pass
        actor = actor_id if actor_id is not None else self.ensure_identity()
        sid = self._adapter.tx_begin(actor)
        session = WorldSession(self._adapter, sid, actor)
        self._current_session = session
        return session

    @property
    def current_session(self) -> Optional[WorldSession]:
        if self._current_session and not self._current_session.closed:
            return self._current_session
        return None

    def close(self) -> None:
        if self._current_session and not self._current_session.closed:
            try:
                self._current_session.abort()
            except Exception:
                pass
        self._adapter.close()

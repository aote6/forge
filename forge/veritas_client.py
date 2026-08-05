"""
DEPRECATED transport shim.

New code must use forge.world.adapter.WorldAdapter / WorldRuntime.
This module remains only for emergency compatibility.
"""

from forge.world.adapter import WorldAdapter
from forge.world.types import ObjectInfo


class VeritasClient:
    """Thin wrapper around WorldAdapter — do not use from Tools."""

    def __init__(self, project_root, binary: str = "veritasd"):
        self._adapter = WorldAdapter(project_root, binary=binary)

    def ping(self) -> bool:
        return self._adapter.ping()

    def list_objects(self) -> list[ObjectInfo]:
        return self._adapter.list_objects()

    def get_object_state(self, object_id: int) -> str | None:
        info = self._adapter.get_object(object_id)
        return None if info is None else info.state

    def object_exists(self, object_id: int) -> bool:
        return self.get_object_state(object_id) is not None

    def create_object(self) -> int | None:
        # Legacy short path via session-less create is not on adapter;
        # use a one-shot session.
        try:
            sid = self._adapter.tx_begin(None)
            oid = self._adapter.tx_create_object(sid)
            self._adapter.tx_commit(sid)
            return oid
        except Exception:
            return None

    def close(self):
        self._adapter.close()

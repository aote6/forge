"""
WorldRuntime — Forge's presence inside the Veritas world.

LLM / Tools must go through this layer, never through WorldAdapter directly.
WorldRuntime does not know about Projection.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from forge.world.adapter import WorldAdapter, WorldAdapterError
from forge.world.identity import IdentityStore
from forge.world.session import WorldSession
from forge.world.types import LinkInfo, ObjectInfo, Receipt, WorldInfo
from forge.world.types import TransactionDelta


class WorldRuntime:
    def __init__(
        self,
        project_root: str | Path = ".",
        binary: str = "veritasd",
        adapter: WorldAdapter | None = None,
        wal_path: str | Path | None = None,
    ):
        self.project_root = str(Path(project_root).expanduser().resolve())
        # Pass wal_path through so adapter always enables durable WAL.
        self._adapter = adapter or WorldAdapter(
            self.project_root, binary=binary, wal_path=wal_path
        )
        self._identity = IdentityStore(self.project_root)
        self._object_id: Optional[int] = None
        self._current_session: Optional[WorldSession] = None
        self._online = False
        try:
            self._online = self._adapter.ping()
        except Exception:
            self._online = False
        self._rebuild_path_map()

    def _rebuild_path_map(self) -> None:
        """Rebuild ObjectPathMap from Veritas receipt history.

        Called on init to recover path→object mapping after restart.
        Depends on durable WAL: receipts_since(0) must return historical
        commits when veritasd was started with VERITAS_WAL.
        """
        try:
            from forge.projections.object_path import ObjectPathMap
        except ImportError:
            return
        try:
            receipts = self.get_receipts_since(0)
        except Exception as e:
            import sys
            print(
                f"[world] path_map rebuild failed: get_receipts_since(0) raised: {e}",
                file=sys.stderr,
            )
            self._path_map_degraded = True
            return
        if not hasattr(self, "_path_map"):
            self._path_map = ObjectPathMap()
        # 重建是 best-effort：单条 delta 失败不应丢掉其余历史映射，
        # 但必须可观测，不能假装 path map 与 World 完全一致。
        self._path_map_degraded = False
        for receipt in receipts:
            delta = getattr(receipt, "delta", None)
            if delta is not None:
                try:
                    self._path_map.update_from_delta(delta)
                except Exception as e:
                    import sys
                    print(
                        f"[world] path_map update_from_delta failed during rebuild "
                        f"(receipt version={getattr(receipt, 'version', '?')}): {e}",
                        file=sys.stderr,
                    )
                    self._path_map_degraded = True

    @property
    def adapter(self) -> WorldAdapter:
        return self._adapter

    @property
    def online(self) -> bool:
        return self._online

    def connect(self) -> bool:
        try:
            self._online = self._adapter.ping()
        except Exception:
            self._online = False
        return self._online

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

    def get_version(self) -> int:
        """Return current world version for crash recovery checks."""
        return self._adapter.world_info().version

    def list_objects(self) -> list[ObjectInfo]:
        return self._adapter.list_objects()

    def get_object(self, object_id: int) -> Optional[ObjectInfo]:
        return self._adapter.get_object(object_id)

    def get_links(self) -> list[LinkInfo]:
        return self._adapter.get_links()

    def begin_session(self, actor_id: Optional[int] = None) -> WorldSession:
        if not self._online:
            raise RuntimeError(
                "veritasd 不在线。世界操作不可用。"
                "请使用本地只读工具（read_file、search_code 等），"
                "或启动 veritasd 后重试。"
            )
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

    def commit_session(self) -> tuple[Receipt, TransactionDelta]:
        if self._current_session is None or self._current_session.closed:
            raise RuntimeError("no active session to commit")
        receipt, delta = self._current_session.commit()
        self._update_path_map(delta)
        return receipt, delta

    def _update_path_map(self, delta):
        try:
            from forge.projections.object_path import ObjectPathMap
        except ImportError:
            return
        if not hasattr(self, "_path_map"):
            self._path_map = ObjectPathMap()
        self._path_map.update_from_delta(delta)

    def get_receipts_since(self, since_version: int) -> list:
        """从 Veritas 获取 version > since_version 的历史 receipt。"""
        resp = self._adapter._send({
            "cmd": "receipts_since",
            "version": since_version
        })
        from forge.world.receipt_parser import parse_receipt
        receipts_raw = resp.get("receipts", [])
        return [parse_receipt({"ok": True, "receipt": r}) for r in receipts_raw]

    def get_path_for_object(self, object_id: int):
        if not hasattr(self, "_path_map"):
            return None
        return self._path_map.get(object_id)

    def find_object_id(self, path: str) -> Optional[int]:
        """Reverse lookup path → object_id via rebuilt ObjectPathMap."""
        if not hasattr(self, "_path_map"):
            return None
        return self._path_map.find_object_id(path)

    def abort_session(self) -> None:
        if self._current_session is None or self._current_session.closed:
            return
        self._current_session.abort()

    def preview_session(self) -> TransactionDelta:
        if self._current_session is None or self._current_session.closed:
            raise RuntimeError("no active session")
        return self._current_session.preview_delta()

    @property
    def current_session(self) -> Optional[WorldSession]:
        if self._current_session and not self._current_session.closed:
            return self._current_session
        return None

    def close(self) -> None:
        """Close session and terminate child veritasd.

        State remains durable under adapter.wal_path; next WorldRuntime
        recovers via the same WAL.
        """
        if self._current_session and not self._current_session.closed:
            try:
                self._current_session.abort()
            except Exception as e:
                import sys
                print(f"[runtime] close: abort session failed: {e}", file=sys.stderr)
        self._adapter.close()

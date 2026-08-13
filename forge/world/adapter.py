"""
World Adapter — sole transport to veritasd.

No other Forge module should talk JSON Lines to veritasd directly.

Persistence contract (from aote6/veritas src/bin/veritasd.rs):
  - If env VERITAS_WAL is set → Kernel::with_wal_path + WorldService::with_wal
  - Else → Kernel::new() / WorldService::new()  (in-memory only)
  - On restart with the same WAL path, recovery rebuilds world from WAL.

Forge MUST set VERITAS_WAL to a stable path under project_root so that
each new veritasd subprocess recovers the same World state.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any, Optional

from forge.world.types import LinkInfo, ObjectInfo, Receipt, WorldInfo, TransactionDelta


class WorldAdapterError(RuntimeError):
    pass


# Default relative WAL path under project_root. Shared by all WorldAdapter
# instances that use the same project_root so state is process-independent.
DEFAULT_WAL_REL = Path(".forge") / "veritas.wal"


class WorldAdapter:
    """JSON Lines client for the Veritas World Interface (veritasd)."""

    def __init__(
        self,
        project_root: str | Path = ".",
        binary: str = "veritasd",
        wal_path: str | Path | None = None,
    ):
        self.root = str(Path(project_root).expanduser().resolve())
        self.binary = self._resolve_binary(binary)
        self._process: subprocess.Popen | None = None
        # Authoritative persistence path. Always set so veritasd recovers.
        if wal_path is not None:
            self.wal_path = str(Path(wal_path).expanduser().resolve())
        else:
            env_wal = os.environ.get("VERITAS_WAL")
            if env_wal:
                self.wal_path = str(Path(env_wal).expanduser().resolve())
            else:
                self.wal_path = str(Path(self.root) / DEFAULT_WAL_REL)
        # Ensure parent dir exists so veritasd can create the file.
        Path(self.wal_path).parent.mkdir(parents=True, exist_ok=True)

    def _resolve_binary(self, binary: str) -> str:
        if Path(binary).exists():
            return str(Path(binary).resolve())
        candidates = [
            Path.home() / "veritas_kernel" / "target" / "release" / "veritasd",
            Path.home() / "veritas" / "target" / "release" / "veritasd",
            Path("/tmp/veritas/target/release/veritasd"),
            Path("/tmp/veritas/target/debug/veritasd"),
        ]
        for c in candidates:
            if c.exists():
                return str(c)
        return binary

    def _ensure_process(self) -> None:
        if self._process is None or self._process.poll() is not None:
            env = os.environ.copy()
            # Critical: without VERITAS_WAL, veritasd uses in-memory Kernel
            # and all state is lost when the subprocess exits.
            env["VERITAS_WAL"] = self.wal_path
            self._process = subprocess.Popen(
                [self.binary],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=self.root,
                env=env,
            )

    def _send(self, request: dict) -> dict:
        self._ensure_process()
        assert self._process and self._process.stdin and self._process.stdout
        line = json.dumps(request, ensure_ascii=False)
        self._process.stdin.write(line + "\n")
        self._process.stdin.flush()
        response_line = self._process.stdout.readline()
        if not response_line:
            err = ""
            if self._process.stderr:
                err = self._process.stderr.read() or ""
            raise WorldAdapterError(
                f"veritasd closed pipe (binary={self.binary}). stderr={err[:500]}"
            )
        response_line = response_line.strip()
        while response_line == "":
            response_line = self._process.stdout.readline().strip()
            if not response_line and self._process.poll() is not None:
                raise WorldAdapterError("veritasd exited unexpectedly")
        try:
            return json.loads(response_line)
        except json.JSONDecodeError as e:
            raise WorldAdapterError(f"bad response: {response_line!r}: {e}") from e

    def close(self) -> None:
        """Terminate the child veritasd process.

        Safe because state is durable on VERITAS_WAL. A subsequent
        WorldAdapter / WorldRuntime will start a new veritasd that
        recovers from the same WAL path.
        """
        if self._process:
            try:
                self._process.terminate()
                try:
                    self._process.wait(timeout=3)
                except Exception:
                    self._process.kill()
            except Exception:
                pass
            self._process = None

    # ---- commands ----

    def ping(self) -> bool:
        resp = self._send({"cmd": "ping"})
        return resp.get("result") == "pong" or resp.get("ok") is True

    def world_info(self) -> WorldInfo:
        resp = self._send({"cmd": "world_info"})
        self._require_ok(resp)
        return WorldInfo(
            version=int(resp.get("version", 0)),
            state_root=int(resp.get("state_root", 0)),
            object_count=int(resp.get("object_count", 0)),
        )

    def list_objects(self) -> list[ObjectInfo]:
        resp = self._send({"cmd": "list_objects"})
        self._require_ok(resp)
        return [
            ObjectInfo(object_id=int(o["id"]), state=str(o["state"]))
            for o in resp.get("objects", [])
        ]

    def get_object(self, object_id: int) -> Optional[ObjectInfo]:
        resp = self._send({"cmd": "get_object", "id": int(object_id)})
        if not resp.get("ok"):
            return None
        obj = resp.get("object") or {}
        return ObjectInfo(object_id=int(obj["id"]), state=str(obj["state"]))

    def get_links(self) -> list[LinkInfo]:
        resp = self._send({"cmd": "get_links"})
        self._require_ok(resp)
        return [
            LinkInfo(
                from_id=int(l["from"]),
                to_id=int(l["to"]),
                link_type=str(l["link_type"]),
            )
            for l in resp.get("links", [])
        ]

    def attach_identity(self, object_id: Optional[int] = None) -> int:
        req: dict[str, Any] = {"cmd": "attach_identity"}
        if object_id is not None:
            req["object_id"] = int(object_id)
        resp = self._send(req)
        self._require_ok(resp)
        return int(resp["object_id"])

    def whoami(self) -> Optional[int]:
        resp = self._send({"cmd": "whoami"})
        if not resp.get("ok"):
            return None
        return int(resp["object_id"])

    def tx_begin(self, actor_id: Optional[int] = None) -> int:
        req: dict[str, Any] = {"cmd": "tx_begin"}
        if actor_id is not None:
            req["actor_id"] = int(actor_id)
        resp = self._send(req)
        self._require_ok(resp)
        return int(resp["session_id"])

    def tx_create_object(self, session_id: int) -> int:
        resp = self._send({"cmd": "tx_create_object", "session_id": int(session_id)})
        self._require_ok(resp)
        return int(resp["object_id"])

    def tx_freeze_object(self, session_id: int, object_id: int) -> None:
        resp = self._send(
            {
                "cmd": "tx_freeze_object",
                "session_id": int(session_id),
                "object_id": int(object_id),
            }
        )
        self._require_ok(resp)

    def tx_death_object(self, session_id: int, object_id: int) -> None:
        resp = self._send(
            {
                "cmd": "tx_death_object",
                "session_id": int(session_id),
                "object_id": int(object_id),
            }
        )
        self._require_ok(resp)

    def tx_link(
        self,
        session_id: int,
        from_id: int,
        to_id: int,
        link_type: str = "owns",
    ) -> None:
        resp = self._send(
            {
                "cmd": "tx_link",
                "session_id": int(session_id),
                "from": int(from_id),
                "to": int(to_id),
                "link_type": link_type,
            }
        )
        self._require_ok(resp)

    def tx_unlink(self, session_id: int, from_id: int, to_id: int) -> None:
        resp = self._send(
            {
                "cmd": "tx_unlink",
                "session_id": int(session_id),
                "from": int(from_id),
                "to": int(to_id),
            }
        )
        self._require_ok(resp)

    def tx_capability_grant(
        self,
        session_id: int,
        grantor: int,
        grantee: int,
        capability_type: str,
        resource: int,
    ) -> None:
        """Forward CapabilityGrant to veritasd. No local authorization logic."""
        resp = self._send(
            {
                "cmd": "tx_capability_grant",
                "session_id": int(session_id),
                "grantor": int(grantor),
                "grantee": int(grantee),
                "capability_type": capability_type,
                "resource": int(resource),
            }
        )
        self._require_ok(resp)

    def tx_write(
        self,
        session_id: int,
        state_id: int,
        value: str | None = None,
        hex_value: str | None = None,
        object_id: int | None = None,
    ) -> None:
        req: dict[str, Any] = {
            "cmd": "tx_write",
            "session_id": int(session_id),
            "state_id": int(state_id),
        }
        if hex_value is not None:
            req["hex"] = hex_value
        elif value is not None:
            req["value"] = value
        else:
            raise WorldAdapterError("tx_write requires value or hex_value")
        if object_id is not None:
            req["object_id"] = int(object_id)
        resp = self._send(req)
        self._require_ok(resp)

    def tx_commit(self, session_id: int) -> Receipt:
        resp = self._send({"cmd": "tx_commit", "session_id": int(session_id)})
        self._require_ok(resp)
        from forge.world.receipt_parser import parse_receipt
        return parse_receipt(resp)

    def tx_abort(self, session_id: int) -> None:
        resp = self._send({"cmd": "tx_abort", "session_id": int(session_id)})
        self._require_ok(resp)

    @staticmethod
    def _require_ok(resp: dict) -> None:
        if not resp.get("ok", False):
            raise WorldAdapterError(str(resp.get("error", resp)))

"""
World Adapter — sole transport to veritasd.

No other Forge module should talk JSON Lines to veritasd directly.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Optional

from forge.world.types import LinkInfo, ObjectInfo, Receipt, WorldInfo


class WorldAdapterError(RuntimeError):
    pass


class WorldAdapter:
    """JSON Lines client for the Veritas World Interface (veritasd)."""

    def __init__(
        self,
        project_root: str | Path = ".",
        binary: str = "veritasd",
    ):
        self.root = str(Path(project_root).expanduser().resolve())
        self.binary = self._resolve_binary(binary)
        self._process: subprocess.Popen | None = None

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
            self._process = subprocess.Popen(
                [self.binary],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd=self.root,
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
        if self._process:
            try:
                self._process.terminate()
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

    def tx_write(
        self,
        session_id: int,
        state_id: int,
        value: str | None = None,
        hex_value: str | None = None,
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
        resp = self._send(req)
        self._require_ok(resp)

    def tx_commit(self, session_id: int) -> Receipt:
        resp = self._send({"cmd": "tx_commit", "session_id": int(session_id)})
        self._require_ok(resp)
        r = resp.get("receipt") or {}
        return Receipt(
            tx_id=int(r.get("tx_id", 0)),
            before_root=int(r.get("before_root", 0)),
            after_root=int(r.get("after_root", 0)),
            version=int(r.get("version", 0)),
        )

    def tx_abort(self, session_id: int) -> None:
        resp = self._send({"cmd": "tx_abort", "session_id": int(session_id)})
        self._require_ok(resp)

    @staticmethod
    def _require_ok(resp: dict) -> None:
        if not resp.get("ok", False):
            raise WorldAdapterError(str(resp.get("error", resp)))

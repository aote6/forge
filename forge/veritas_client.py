"""
VeritasClient —— JSON Lines RPC 协议，连接 veritasd 常驻进程。
"""
import json
import subprocess
from pathlib import Path
from dataclasses import dataclass


@dataclass
class ObjectInfo:
    object_id: int
    state: str


class VeritasClient:
    """通过 veritasd JSON Lines 协议与 Kernel 通信。"""

    def __init__(self, project_root: str | Path, binary: str = "veritasd"):
        self.root = str(project_root)
        if not Path(binary).exists():
            default = Path.home() / "veritas_kernel" / "target" / "release" / "veritasd"
            if default.exists():
                binary = str(default)
        self.binary = binary
        self._process: subprocess.Popen | None = None

    def _ensure_process(self):
        if self._process is None or self._process.poll() is not None:
            self._process = subprocess.Popen(
                [self.binary],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                cwd=self.root,
            )

    def _send(self, request: dict) -> dict:
        self._ensure_process()
        assert self._process and self._process.stdin and self._process.stdout
        line = json.dumps(request)
        self._process.stdin.write(line + "\n")
        self._process.stdin.flush()
        response_line = self._process.stdout.readline().strip()
        # 跳过空行（veritasd stderr 日志不应出现在 stdout，但以防万一）
        while response_line == '':
            response_line = self._process.stdout.readline().strip()
        return json.loads(response_line)

    def ping(self) -> bool:
        resp = self._send({"cmd": "ping"})
        return resp.get("result") == "pong"

    def list_objects(self) -> list[ObjectInfo]:
        resp = self._send({"cmd": "list_objects"})
        objects = resp.get("objects", [])
        return [
            ObjectInfo(object_id=obj["id"], state=obj["state"])
            for obj in objects
        ]

    def get_object_state(self, object_id: int) -> str | None:
        resp = self._send({"cmd": "get_object", "id": object_id})
        if not resp.get("ok"):
            return None
        obj = resp.get("object", {})
        return obj.get("state")

    def object_exists(self, object_id: int) -> bool:
        return self.get_object_state(object_id) is not None

    def create_object(self) -> int | None:
        """创建新 Object，返回 object_id"""
        resp = self._send({"cmd": "create_object"})
        if resp.get("ok"):
            obj = resp.get("object", {})
            return obj.get("id")
        return None

    def close(self):
        if self._process:
            self._process.terminate()
            self._process = None

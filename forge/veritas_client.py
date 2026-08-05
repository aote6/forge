"""
VeritasClient —— 通过常驻 veritasd 进程与 Kernel 通信。
"""
import subprocess
from pathlib import Path
from dataclasses import dataclass


@dataclass
class ObjectInfo:
    object_id: int
    state: str


class VeritasClient:
    """通过 veritasd stdin/stdout 协议与 Kernel 通信。"""

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

    def _send(self, request: str) -> str:
        self._ensure_process()
        assert self._process and self._process.stdin
        self._process.stdin.write(request + "\n")
        self._process.stdin.flush()
        assert self._process.stdout
        return self._process.stdout.readline().strip()

    def ping(self) -> bool:
        return self._send("ping") == "pong"

    def list_objects(self) -> list[ObjectInfo]:
        output = self._send("list_objects")
        if not output or output == "(no objects)":
            return []
        objs = []
        for line in output.split("\n"):
            parts = line.strip().split()
            if len(parts) >= 2:
                try:
                    objs.append(ObjectInfo(
                        object_id=int(parts[0]),
                        state=parts[1]
                    ))
                except ValueError:
                    pass
        return objs

    def get_object_state(self, object_id: int) -> str | None:
        output = self._send(f"get_object {object_id}")
        if "not found" in output:
            return None
        parts = output.strip().split()
        if len(parts) >= 2:
            return parts[1]
        return None

    def object_exists(self, object_id: int) -> bool:
        return self.get_object_state(object_id) is not None

    def close(self):
        if self._process:
            self._process.terminate()
            self._process = None

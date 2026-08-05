"""
VeritasClient —— 通过 CLI 调用 Veritas Kernel。
"""
import subprocess
from pathlib import Path
from dataclasses import dataclass


@dataclass
class ObjectInfo:
    object_id: int
    state: str


class VeritasClient:
    """通过 veritas inspect 命令读取 Kernel 世界状态。"""

    def __init__(self, project_root: str | Path, binary: str = "veritas"):
        self.root = str(project_root)
        # 优先用绝对路径
        if not Path(binary).exists():
            default = Path.home() / "veritas_kernel" / "target" / "release" / "veritas"
            if default.exists():
                binary = str(default)
        self.binary = binary

    def _run(self, *args: str) -> str:
        result = subprocess.run(
            [self.binary] + list(args),
            cwd=self.root,
            capture_output=True,
            text=True,
            timeout=10
        )
        return result.stdout.strip()

    def list_objects(self) -> list[ObjectInfo]:
        output = self._run("inspect", "list")
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
        output = self._run("inspect", "object", str(object_id))
        if "not found" in output:
            return None
        parts = output.strip().split()
        if len(parts) >= 2:
            return parts[1]
        return None

    def object_exists(self, object_id: int) -> bool:
        return self.get_object_state(object_id) is not None

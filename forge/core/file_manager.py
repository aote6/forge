"""文件管理器 - 只负责纯文件 IO

单文件写入使用「同目录临时文件 + os.replace」原子替换，避免：
- 写到一半进程崩溃留下半截文件；
- 与外部并发打开同一路径时的非原子覆盖窗口。
os.replace 在同一文件系统上是原子的（POSIX / Windows）。
"""
import os
import tempfile


class FileManager:
    def read(self, path: str) -> str:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()

    def read_lines(self, path: str, start: int, end: int) -> str:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
        total = len(lines)
        start_idx = max(0, start - 1)
        end_idx = min(total, end) if end > 0 else total
        return "".join(lines[start_idx:end_idx])

    def write(self, path: str, content: str):
        """原子写入：先写同目录临时文件，再 os.replace 到目标路径。"""
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        dir_name = os.path.dirname(os.path.abspath(path)) or "."
        fd, tmp_path = tempfile.mkstemp(
            dir=dir_name,
            prefix=".forge_write_",
            suffix=".tmp",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_path, path)
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def exists(self, path: str) -> bool:
        return os.path.exists(path)

    def size(self, path: str) -> int:
        return os.path.getsize(path) if self.exists(path) else 0

    def line_count(self, path: str) -> int:
        if not self.exists(path):
            return 0
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return sum(1 for _ in f)

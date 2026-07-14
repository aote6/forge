"""文件管理器 - 只负责纯文件 IO"""
import os


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
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
    
    def exists(self, path: str) -> bool:
        return os.path.exists(path)
    
    def size(self, path: str) -> int:
        return os.path.getsize(path) if self.exists(path) else 0
    
    def line_count(self, path: str) -> int:
        if not self.exists(path):
            return 0
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            return sum(1 for _ in f)

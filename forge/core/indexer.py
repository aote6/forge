"""代码搜索抽象"""
import subprocess
import shutil
from abc import ABC, abstractmethod


class BaseIndexer(ABC):
    @abstractmethod
    def search(self, pattern: str, path: str = ".") -> str:
        pass


class RipgrepIndexer(BaseIndexer):
    def search(self, pattern: str, path: str = ".") -> str:
        try:
            r = subprocess.run(
                ["rg", "-n", "--no-heading", "-m", "20", pattern, path],
                capture_output=True, text=True, timeout=10
            )
            return (r.stdout + r.stderr).strip()[:3000] or "无结果"
        except Exception as e:
            return f"搜索出错: {e}"


class GrepIndexer(BaseIndexer):
    def search(self, pattern: str, path: str = ".") -> str:
        try:
            r = subprocess.run(
                ["grep", "-Rn", "-m", "20", pattern, path],
                capture_output=True, text=True, timeout=10
            )
            return (r.stdout + r.stderr).strip()[:3000] or "无结果"
        except Exception as e:
            return f"搜索出错: {e}"


class AutoIndexer(BaseIndexer):
    def __init__(self):
        if shutil.which("rg"):
            self._indexer = RipgrepIndexer()
        else:
            self._indexer = GrepIndexer()
    
    def search(self, pattern: str, path: str = ".") -> str:
        return self._indexer.search(pattern, path)

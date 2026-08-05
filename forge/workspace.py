"""Workspace - 所有文件/代码操作的统一入口"""
import os
from pathlib import Path
from forge.core.file_manager import FileManager
from forge.core.patch_engine import PatchEngine
from forge.core.validator import ValidatorRegistry
from forge.core.backup_manager import BackupManager
from forge.core.transaction import TransactionManager, MemoryTransactionStore
from forge.core.indexer import AutoIndexer, BaseIndexer
from forge.core.security import is_blocked_path


class Workspace:
    def __init__(self, project_root: str = "."):
        self.project_root = os.path.abspath(os.path.expanduser(project_root))
        self.fm = FileManager()
        self.patch_engine = PatchEngine()
        self.validator = ValidatorRegistry
        self.backup = BackupManager()
        self.indexer: BaseIndexer = AutoIndexer()
        self.transactions = TransactionManager(
            fm=self.fm,
            patch_engine=self.patch_engine,
            validator=self.validator,
            backup_mgr=self.backup,
            store=MemoryTransactionStore()
        )

    def _resolve(self, path: str) -> str:
        p = Path(os.path.expanduser(path))
        resolved = p if p.is_absolute() else Path(self.project_root) / p
        blocked = is_blocked_path(str(resolved))
        if blocked:
            raise PermissionError(f"路径被安全策略拦截（命中规则: {blocked}）: {resolved}")
        return str(resolved)

    def read_file(self, path: str, start: int = 1, end: int = 0) -> str:
        path = self._resolve(path)
        if end == 0:
            return self.fm.read(path)
        return self.fm.read_lines(path, start, end)

    def prepare_write(self, path: str, operations: list):
        return self.transactions.prepare(self._resolve(path), operations)

    def commit_write(self, tx_id: str):
        return self.transactions.commit(tx_id)

    def cancel_write(self, tx_id: str):
        return self.transactions.cancel(tx_id)

    def search_code(self, pattern: str, path: str = ".") -> str:
        return self.indexer.search(pattern, self._resolve(path))

"""事务管理"""
import time
import hashlib
import ulid
from forge.core.patch_engine import PatchEngine
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Transaction:
    id: str
    path: str
    operations: list
    original_hash: str
    patch: str
    status: str = "pending"
    created_at: float = field(default_factory=time.time)
    committed_at: Optional[float] = None


class TransactionStore(ABC):
    @abstractmethod
    def create(self, tx: Transaction) -> str: ...
    @abstractmethod
    def get(self, tx_id: str) -> Optional[Transaction]: ...
    @abstractmethod
    def update(self, tx: Transaction): ...
    @abstractmethod
    def delete(self, tx_id: str): ...
    @abstractmethod
    def list_pending(self) -> list: ...


class MemoryTransactionStore(TransactionStore):
    def __init__(self):
        self._transactions: dict = {}
    
    def create(self, tx: Transaction) -> str:
        self._transactions[tx.id] = tx
        return tx.id
    
    def get(self, tx_id: str) -> Optional[Transaction]:
        return self._transactions.get(tx_id)
    
    def update(self, tx: Transaction):
        self._transactions[tx.id] = tx
    
    def delete(self, tx_id: str):
        self._transactions.pop(tx_id, None)
    
    def list_pending(self) -> list:
        return [t for t in self._transactions.values() if t.status == "pending"]


class TransactionManager:
    def __init__(self, fm, patch_engine, validator, backup_mgr, store=None):
        self.fm = fm
        self.patch_engine = patch_engine
        self.validator = validator
        self.backup = backup_mgr
        self.store = store or MemoryTransactionStore()
    
    def prepare(self, path: str, operations: list) -> tuple:
        try:
            original = self.fm.read(path)
        except FileNotFoundError:
            return False, f"文件不存在: {path}", None
        except Exception as e:
            return False, f"读取失败: {e}", None
        
        ok, new_content = self._apply_operations(original, operations)
        if not ok:
            return False, new_content, None
        
        patch = self.patch_engine.diff(original, new_content, path)
        original_hash = hashlib.sha256(original.encode()).hexdigest()
        
        tx = Transaction(
            id=str(ulid.ULID()),
            path=path,
            operations=operations,
            original_hash=original_hash,
            patch=patch,
        )
        self.store.create(tx)
        return True, f"事务 {tx.id} 已准备", tx
    
    def commit(self, tx_id: str) -> tuple:
        tx = self.store.get(tx_id)
        if not tx or tx.status != "pending":
            return False, f"事务不存在或状态异常: {tx_id}"
        
        try:
            current = self.fm.read(tx.path)
            current_hash = hashlib.sha256(current.encode()).hexdigest()
        except Exception as e:
            return False, f"读取文件失败: {e}"
        
        if current_hash != tx.original_hash:
            return False, "❌ 文件已被外部修改，事务失效。请重新 prepare_write。"
        
        new_content = self._apply_operations_get_content(current, tx.operations)
        
        backup_path = self.backup.backup(tx.path)
        if not backup_path:
            return False, "❌ 备份失败，为安全起见拒绝提交（无法保证可回滚，请检查备份目录权限/磁盘空间）"
        try:
            self.fm.write(tx.path, new_content)
        except Exception as e:
            return False, f"写入失败: {e}"
        
        ok, msg = self.validator.validate(tx.path)
        if not ok:
            if backup_path:
                self.backup.restore(backup_path, tx.path)
            tx.status = "cancelled"
            self.store.update(tx)
            return False, f"❌ 校验失败，已自动回滚。\n{msg}"
        
        tx.status = "committed"
        tx.committed_at = time.time()
        self.store.update(tx)
        return True, f"✅ 提交成功 + 校验通过"
    
    def cancel(self, tx_id: str) -> tuple:
        tx = self.store.get(tx_id)
        if not tx:
            return False, f"事务不存在: {tx_id}"
        tx.status = "cancelled"
        self.store.update(tx)
        return True, f"事务 {tx_id} 已取消"
    
    def _apply_operations(self, original: str, operations: list) -> tuple:
        lines = original.splitlines(keepends=True)
        for op in operations:
            op_type = op.get("type", "replace")
            anchor = op.get("anchor", "")
            target = op.get("target", "")
            value = op.get("value", "")
            
            matches = [i for i, line in enumerate(lines) if anchor in line]
            if len(matches) == 0:
                return False, f"找不到 anchor: '{anchor}'"
            if len(matches) > 1:
                line_nums = [m + 1 for m in matches]
                return False, (
                    f"anchor '{anchor}' 存在歧义，在文件中出现 {len(matches)} 次"
                    f"（行号: {line_nums}）。请提供更具体的 anchor（如包含更多上下文）。"
                )
            anchor_line = matches[0]
            
            target_line = -1
            for i in range(anchor_line, min(len(lines), anchor_line + 100)):
                if target in lines[i]:
                    target_line = i
                    break
            if target_line == -1:
                return False, f"在 '{anchor}' 附近找不到 target: '{target}'"
            
            if op_type == "replace":
                lines[target_line] = lines[target_line].replace(target, value)
            elif op_type == "delete":
                lines.pop(target_line)
            elif op_type == "insert_before":
                lines.insert(target_line, value + "\n")
            elif op_type == "insert_after":
                lines.insert(target_line + 1, value + "\n")
        
        return True, "".join(lines)
    
    def _apply_operations_get_content(self, original: str, operations: list) -> str:
        ok, content = self._apply_operations(original, operations)
        return content if ok else original

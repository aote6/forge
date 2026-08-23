"""备份管理器"""
import os
import shutil
import sys
import datetime
from pathlib import Path
from typing import Optional


class BackupManager:
    def __init__(self, backup_dir: str = ".forge/backups"):
        self.backup_dir = Path(backup_dir)
        self.backup_dir.mkdir(parents=True, exist_ok=True)

    def backup(self, path: str) -> Optional[str]:
        if not os.path.exists(path):
            return None
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        fname = os.path.basename(path)
        dest = self.backup_dir / f"{fname}.{ts}.bak"
        shutil.copy2(path, str(dest))
        return str(dest)

    def restore(self, backup_path: str, target_path: str) -> bool:
        try:
            shutil.copy2(backup_path, target_path)
            return True
        except Exception as e:
            print(f"[backup] restore failed: {e}", file=sys.stderr)
            return False

    def restore_latest(self, target_path: str) -> bool:
        """将最近一次 backup(target_path) 的内容写回 target_path。

        按备份文件名排序取最新；无备份时返回 False。
        """
        fname = os.path.basename(target_path)
        candidates = sorted(
            self.backup_dir.glob(f"{fname}.*.bak"),
            key=lambda p: p.name,
            reverse=True,
        )
        if not candidates:
            return False
        return self.restore(str(candidates[0]), target_path)

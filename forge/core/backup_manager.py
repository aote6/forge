"""备份管理器"""
import os
import shutil
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
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        fname = os.path.basename(path)
        dest = self.backup_dir / f"{fname}.{ts}.bak"
        shutil.copy2(path, str(dest))
        return str(dest)
    
    def restore(self, backup_path: str, target_path: str) -> bool:
        try:
            shutil.copy2(backup_path, target_path)
            return True
        except Exception:
            return False

"""Recovery — 启动时同步状态检测（不再 replay receipt 写磁盘）。"""

from forge.recovery.check import RecoveryCheck

__all__ = ["RecoveryCheck"]

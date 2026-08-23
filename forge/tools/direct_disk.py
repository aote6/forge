"""P2-1: 无 Veritas 时的一等直写路径（direct_disk）。

范围（刻意很窄，不要扩大）：
- 只解决「veritasd 不可用时，文件内容还能不能写下去」。
- World object 操作（create_object / link_objects / unlink_objects）没有磁盘
  等价物，veritasd 不可用时必须继续硬失败，不得伪装成 direct_disk。
- Veritas 可用时不参与任何决策：探测返回 True，调用方走原有 World 事务路径。

direct_disk 写入不产生 World receipt，因此是「有痕但 World 未记录」的变更：
result/display 必须显式标注 mode=direct_disk，并引导恢复 veritasd 后 forge_sync 对账。
"""
from __future__ import annotations

import time
from pathlib import Path

MODE_DIRECT_DISK = "direct_disk"

# veritasd 不可用时仍可继续执行的工具集合。
# str_replace / write_file：走 direct_disk 本地写盘。
# undo_last_tx：本来就只依赖 .forge/tx_shadow，从不触碰 World，
#               World 不可达时更需要放行（否则直写改错了无法回滚）。
DIRECT_DISK_TOOLS = frozenset({"str_replace", "write_file", "undo_last_tx"})


def world_available(world) -> bool:
    """Veritas/veritasd 是否可用。

    探测口径与 SyncLayer.world_available 一致（get_version 往返一次）。
    无法探测时（对象没有 get_version / online）返回 True —— 默认假定可用，
    保证既有 Veritas 事务路径与既有测试 fixture 行为完全不变。
    """
    if world is None:
        return False
    probe = getattr(world, "get_version", None)
    if callable(probe):
        try:
            probe()
            return True
        except Exception:
            return False
    online = getattr(world, "online", None)
    if isinstance(online, bool):
        return online
    return True


_TX_SEQ = 0


def next_tx_id() -> str:
    """direct_disk 的合成 tx 标识。

    不是 Veritas 事务号，只用于 shadow undo 栈与 session_changes 的可追溯性，
    因此必须进程内唯一（tx_shadow 用 tx_id 拼 .pre 文件名）。
    """
    global _TX_SEQ
    _TX_SEQ += 1
    return f"direct-{time.time_ns()}-{_TX_SEQ}"


def write_text(project_root: str, path: str, content: str) -> str:
    """直接写盘（建父目录）。失败向上抛 OSError，由调用方转成 ToolResult.fail。"""
    target = Path(project_root) / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content if content is not None else "", encoding="utf-8")
    return str(target)

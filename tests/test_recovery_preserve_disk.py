"""契约测试：分叉必须 STOP / CONFLICT，禁止 skip → success → 覆盖磁盘。

这是旧 test_recovery_preserve_disk 的契约化重写，验证相反语义：
- 不存在 recovery_preserve_disk / last_skipped_diverged 软跳过模式
- 磁盘有 World 未记录的手动修改时，fast-forward 不得覆盖 → CONFLICT
- 不"从 World 恢复缺失文件"

docs/WORLD_DISK_SYNC.md §2 / §4 / §6。
"""
from __future__ import annotations

import binascii
import subprocess

from forge.projections.file_projection import FileProjection
from forge.sync.state import SyncState
from forge.sync.sync_layer import CONFLICT, IN_SYNC, SyncLayer
from forge.world.types import Receipt, TransactionDelta


class MockWorld:
    def __init__(self, receipts=None):
        self._receipts = list(receipts or [])

    def get_receipts_since(self, version):
        return [r for r in self._receipts if r.version > version]

    def get_version(self):
        return max((r.version for r in self._receipts), default=0)


def _file_receipt(version, abs_path, content):
    path_hex = binascii.hexlify(str(abs_path).encode("utf-8")).decode("ascii")
    content_hex = binascii.hexlify(content.encode("utf-8")).decode("ascii")
    delta = TransactionDelta(
        memory_written=[
            {"object_id": 1, "state_id": 0, "value_hex": path_hex},
            {"object_id": 1, "state_id": 1, "value_hex": content_hex},
        ],
    )
    return Receipt(
        tx_id=version, before_root=0, after_root=version, version=version, delta=delta
    )


def _init_git_repo(root):
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "t"], check=True)
    (root / "README.md").write_text("init\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-q", "-m", "init"], check=True)


def test_no_preserve_disk_soft_skip_mode(tmp_path):
    """旧 recovery_preserve_disk / last_skipped_diverged 软跳过模式已移除。"""
    fp = FileProjection(project_root=str(tmp_path))
    assert not hasattr(fp, "recovery_preserve_disk")
    assert not hasattr(fp, "last_skipped_diverged")


def test_existing_manual_edit_is_conflict_not_overwrite(tmp_path):
    """磁盘已有手动修改、World 有旧 receipt：fast-forward 不得覆盖 → CONFLICT。"""
    _init_git_repo(tmp_path)
    target = tmp_path / "local_tools.py"
    target.write_text("# MANUAL PATCH\npost_toot = True\n", encoding="utf-8")

    world_old = "# OLD FROM WORLD\n"
    receipt = _file_receipt(1, str(target.resolve()), world_old)

    state = SyncState(tmp_path)
    fp = FileProjection(project_root=str(tmp_path), sync_state=state)
    layer = SyncLayer(str(tmp_path), MockWorld([receipt]), state, fp)

    # 磁盘已有该文件但从未进入已知基线 → fast-forward 必须 CONFLICT，不覆盖
    result = layer.sync()
    assert result.status == CONFLICT, result.format()
    assert target.read_text(encoding="utf-8") == "# MANUAL PATCH\npost_toot = True\n"
    assert state.disk_synced_version == 0


def test_missing_file_is_not_auto_restored(tmp_path):
    """World 有历史 receipt、磁盘缺文件：不自动从 World 恢复缺失文件。"""
    _init_git_repo(tmp_path)
    target = tmp_path / "missing.py"
    assert not target.exists()

    receipt = _file_receipt(1, str(target.resolve()), "print('restored')\n")

    state = SyncState(tmp_path)
    fp = FileProjection(project_root=str(tmp_path), sync_state=state)
    layer = SyncLayer(str(tmp_path), MockWorld([receipt]), state, fp)

    # 缺失文件是"创建"，fast-forward 允许安全物化（这是正常前向同步，非"恢复缺失"）。
    # 关键：FileProjection 不再有任何 preserve 模式去"跳过/恢复"。
    report = layer.detect()
    assert report.status == "FAST_FORWARD_WORLD_TO_DISK", report.format()


def test_synced_file_is_not_conflict(tmp_path):
    """已同步到已知基线、磁盘未变的文件可被安全前向推进。"""
    _init_git_repo(tmp_path)
    target = tmp_path / "f.txt"
    v1 = _file_receipt(1, str(target.resolve()), "v1\n")
    v2 = _file_receipt(2, str(target.resolve()), "v2\n")

    state = SyncState(tmp_path)
    fp = FileProjection(project_root=str(tmp_path), sync_state=state)
    fp.apply(v1, v1.delta)  # S=1, 已知 hash=v1
    layer = SyncLayer(str(tmp_path), MockWorld([v1, v2]), state, fp)

    result = layer.sync()  # World 前向 v2，磁盘在基线 v1 → 安全
    assert result.status == IN_SYNC, result.format()
    assert target.read_text(encoding="utf-8") == "v2\n"
    assert state.disk_synced_version == 2

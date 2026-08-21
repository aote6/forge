"""契约测试：Recovery 只做启动同步状态检测，不 replay receipt 写磁盘。

验证 docs/WORLD_DISK_SYNC.md §1 / §3 / §4 / §6：
- RecoveryCheck 不把 World receipt 当磁盘恢复指令（不写盘、不恢复缺失文件）
- 分叉 → CONFLICT / STOP，绝不 skip → success → 推进水位
- checkpoint 水位拆分：receipt_consumed_version vs disk_synced_version
- Receipt.source 默认 forge_tool

不依赖 veritasd；用 MockWorld + 临时 git 仓库。
"""
from __future__ import annotations

import binascii
import subprocess

from forge.projections.base import Projection, ProjectionManager, ProjectionResult
from forge.projections.file_projection import FileProjection
from forge.recovery import RecoveryCheck
from forge.sync.state import SyncState
from forge.sync.sync_layer import (
    CONFLICT,
    FAST_FORWARD_WORLD_TO_DISK,
    SyncLayer,
)
from forge.world.types import Receipt, TransactionDelta


class MockWorld:
    def __init__(self, receipts=None):
        self._receipts = list(receipts or [])

    def get_receipts_since(self, version):
        return [r for r in self._receipts if r.version > version]

    def get_version(self):
        return max((r.version for r in self._receipts), default=0)


class _RecordingProjection(Projection):
    def __init__(self, name="rec"):
        self._name = name

    @property
    def name(self):
        return self._name

    def prepare(self, delta):
        return None

    def apply(self, receipt, delta):
        return ProjectionResult(name=self.name, success=True)


def _file_receipt(version, abs_path, content, source="forge_tool"):
    path_hex = binascii.hexlify(str(abs_path).encode("utf-8")).decode("ascii")
    content_hex = binascii.hexlify(content.encode("utf-8")).decode("ascii")
    delta = TransactionDelta(
        memory_written=[
            {"object_id": 1, "state_id": 0, "value_hex": path_hex},
            {"object_id": 1, "state_id": 1, "value_hex": content_hex},
        ],
    )
    return Receipt(
        tx_id=version, before_root=0, after_root=version,
        version=version, delta=delta, source=source,
    )


def _init_git_repo(root):
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "t"], check=True)
    (root / "README.md").write_text("init\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-q", "-m", "init"], check=True)


def test_recovery_check_is_detection_only(tmp_path):
    """启动检测不写磁盘、不推进任何水位。"""
    _init_git_repo(tmp_path)
    target = tmp_path / "file.txt"
    receipt = _file_receipt(1, str(target.resolve()), "v1\n")

    state = SyncState(tmp_path)
    fp = FileProjection(project_root=str(tmp_path), sync_state=state)
    layer = SyncLayer(str(tmp_path), MockWorld([receipt]), state, fp)

    report = RecoveryCheck(layer).check()
    assert report.status == FAST_FORWARD_WORLD_TO_DISK, report.format()
    # 检测只读：未物化文件，未推进 disk_synced_version
    assert not target.exists()
    assert state.disk_synced_version == 0


def test_recovery_check_conflict_does_not_advance(tmp_path):
    """分叉时启动检测报告 CONFLICT，且不推进水位。"""
    _init_git_repo(tmp_path)
    target = tmp_path / "file.txt"
    v1 = _file_receipt(1, str(target.resolve()), "v1\n")
    v2 = _file_receipt(2, str(target.resolve()), "WORLD v2\n")

    state = SyncState(tmp_path)
    fp = FileProjection(project_root=str(tmp_path), sync_state=state)
    fp.apply(v1, v1.delta)  # 到 IN_SYNC, S=1
    layer = SyncLayer(str(tmp_path), MockWorld([v1, v2]), state, fp)

    target.write_text("USER EDIT\n", encoding="utf-8")  # 外部改盘
    report = RecoveryCheck(layer).check()
    assert report.status == CONFLICT, report.format()
    assert state.disk_synced_version == 1  # 不推进


def test_checkpoint_split_receipt_consumed_vs_disk_synced(tmp_path):
    """receipt_consumed_version（checkpoint）与 disk_synced_version 是两套水位。"""
    ckpt_dir = tmp_path / ".forge"
    pm = ProjectionManager(checkpoint_dir=str(ckpt_dir))
    fp = FileProjection(project_root=str(tmp_path))  # 无 sync_state
    pm.register(fp)
    pm.register(_RecordingProjection("rec"))

    target = tmp_path / "f.txt"
    receipt = _file_receipt(1, str(target.resolve()), "x\n")
    results = pm.project(receipt, receipt.delta)
    assert all(r.success for r in results)

    # receipt_consumed_version 推进（projection bookkeeping）
    assert pm.checkpoint.checkpoints["file"] == 1
    assert pm.checkpoint.checkpoints["rec"] == 1
    # disk_synced_version 是独立 store，未在这里推进（FileProjection 无 sync_state）
    assert not (ckpt_dir / "sync_state.json").exists()


def test_receipt_source_defaults_forge_tool():
    """Receipt.source 默认 forge_tool（契约 §6）。"""
    r = Receipt(tx_id=1, before_root=0, after_root=0, version=1)
    assert r.source == "forge_tool"


if __name__ == "__main__":
    import sys
    sys.exit("run with pytest")

"""契约测试：SyncLayer 三态同步判定（docs/WORLD_DISK_SYNC.md §3/§4/§6/§7）。

覆盖：
- IN_SYNC
- FAST_FORWARD(World → Disk)（崩溃后安全推进）
- FAST_FORWARD(Disk → World)（外部编辑 / 外部 git commit）
- CONFLICT（双方在共同已知状态之后都前进 → 停止，不覆盖）
- external_sync 记录真实外部同步事实，不伪造 World transaction

这些测试不依赖 veritasd；用 MockWorld + 临时 git 仓库。
"""
from __future__ import annotations

import binascii
import subprocess

from forge.sync.state import SyncState
from forge.sync.sync_layer import (
    CONFLICT,
    FAST_FORWARD_DISK_TO_WORLD,
    FAST_FORWARD_WORLD_TO_DISK,
    IN_SYNC,
    WORLD_UNAVAILABLE,
    SyncLayer,
)
from forge.projections.file_projection import FileProjection
from forge.world.types import Receipt, TransactionDelta


class MockWorld:
    """模拟 WorldRuntime：返回可控历史 receipt。"""

    def __init__(self, receipts=None):
        self._receipts = list(receipts or [])

    def get_receipts_since(self, version):
        return [r for r in self._receipts if r.version > version]

    def get_version(self):
        return max((r.version for r in self._receipts), default=0)


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
        tx_id=version,
        before_root=0,
        after_root=version,
        version=version,
        delta=delta,
        source=source,
    )


def _init_git_repo(root):
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "t"], check=True)
    (root / "README.md").write_text("init\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-q", "-m", "init"], check=True)


def _head(root):
    r = subprocess.run(
        ["git", "-C", str(root), "rev-parse", "HEAD"],
        capture_output=True, text=True,
    )
    return r.stdout.strip()


def _layer(root, world, state, fp):
    return SyncLayer(project_root=str(root), world_runtime=world, sync_state=state, file_projection=fp)


def test_in_sync_after_forge_write(tmp_path):
    """正常 forge 写盘后：World 与 Disk/Git 同处已知状态 → IN_SYNC。"""
    _init_git_repo(tmp_path)
    target = tmp_path / "file.txt"
    receipt = _file_receipt(1, str(target.resolve()), "v1\n")

    state = SyncState(tmp_path)
    fp = FileProjection(project_root=str(tmp_path), sync_state=state)
    # 模拟正常路径：用户确认 → commit → projection 写盘
    res = fp.apply(receipt, receipt.delta)
    assert res.success, res.reason

    layer = _layer(tmp_path, MockWorld([receipt]), state, fp)
    report = layer.detect()
    assert report.status == IN_SYNC, report.format()
    assert state.disk_synced_version == 1
    assert target.read_text(encoding="utf-8") == "v1\n"


def test_fast_forward_world_to_disk_after_crash(tmp_path):
    """崩溃后 World 有未物化 receipt、磁盘未变 → FAST_FORWARD(World → Disk) 可安全推进。"""
    _init_git_repo(tmp_path)
    target = tmp_path / "file.txt"
    receipt = _file_receipt(1, str(target.resolve()), "v1\n")

    state = SyncState(tmp_path)  # S=0，未写盘
    fp = FileProjection(project_root=str(tmp_path), sync_state=state)
    layer = _layer(tmp_path, MockWorld([receipt]), state, fp)

    report = layer.detect()
    assert report.status == FAST_FORWARD_WORLD_TO_DISK, report.format()

    result = layer.sync()
    assert result.status == IN_SYNC, result.format()
    assert target.read_text(encoding="utf-8") == "v1\n"
    assert state.disk_synced_version == 1


def test_fast_forward_disk_to_world_external_edit(tmp_path):
    """外部编辑已知文件、World 未变 → FAST_FORWARD(Disk → World)，记录 external_sync。"""
    _init_git_repo(tmp_path)
    target = tmp_path / "file.txt"
    receipt = _file_receipt(1, str(target.resolve()), "v1\n")

    state = SyncState(tmp_path)
    fp = FileProjection(project_root=str(tmp_path), sync_state=state)
    fp.apply(receipt, receipt.delta)  # 先到 IN_SYNC
    layer = _layer(tmp_path, MockWorld([receipt]), state, fp)
    assert layer.detect().status == IN_SYNC

    # 外部修改磁盘
    target.write_text("USER EDIT\n", encoding="utf-8")
    report = layer.detect()
    assert report.status == FAST_FORWARD_DISK_TO_WORLD, report.format()

    result = layer.sync()
    assert result.status == IN_SYNC, result.format()
    # 外部同步事实：source=external_sync，且不伪造 World transaction
    assert state.last_sync["source"] == "external_sync"
    assert layer._world.get_version() == 1  # World 没有新增 receipt
    assert target.read_text(encoding="utf-8") == "USER EDIT\n"


def test_fast_forward_disk_to_world_external_commit(tmp_path):
    """外部 git commit（HEAD 前进）、World 未变 → FAST_FORWARD(Disk → World)。"""
    _init_git_repo(tmp_path)
    target = tmp_path / "file.txt"
    receipt = _file_receipt(1, str(target.resolve()), "v1\n")

    state = SyncState(tmp_path)
    fp = FileProjection(project_root=str(tmp_path), sync_state=state)
    fp.apply(receipt, receipt.delta)
    layer = _layer(tmp_path, MockWorld([receipt]), state, fp)
    assert layer.detect().status == IN_SYNC

    c0 = state.last_known_commit
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-q", "-m", "ext"], check=True)

    report = layer.detect()
    assert report.status == FAST_FORWARD_DISK_TO_WORLD, report.format()

    result = layer.sync()
    assert result.status == IN_SYNC, result.format()
    assert state.last_known_commit == _head(tmp_path)
    assert state.last_known_commit != c0


def test_conflict_when_both_advance(tmp_path):
    """World 与 Disk 在共同已知状态之后都前进 → CONFLICT，禁止覆盖磁盘。"""
    _init_git_repo(tmp_path)
    target = tmp_path / "file.txt"
    receipt_v1 = _file_receipt(1, str(target.resolve()), "v1\n")
    receipt_v2 = _file_receipt(2, str(target.resolve()), "WORLD v2\n")

    state = SyncState(tmp_path)
    fp = FileProjection(project_root=str(tmp_path), sync_state=state)
    fp.apply(receipt_v1, receipt_v1.delta)  # 到 IN_SYNC, S=1
    layer1 = _layer(tmp_path, MockWorld([receipt_v1]), state, fp)
    assert layer1.detect().status == IN_SYNC

    # World 前进（新增 v2）+ 磁盘外部修改
    target.write_text("USER EDIT\n", encoding="utf-8")
    layer = _layer(tmp_path, MockWorld([receipt_v1, receipt_v2]), state, fp)
    report = layer.detect()
    assert report.status == CONFLICT, report.format()

    result = layer.sync()
    assert result.status == CONFLICT, result.format()
    # 磁盘未被 World 覆盖；水位不推进
    assert target.read_text(encoding="utf-8") == "USER EDIT\n"
    assert state.disk_synced_version == 1


def test_not_a_git_repo(tmp_path):
    """非 git 工作区 → NOT_A_GIT_REPO，不做无 Git 的第二套状态机（决策 5）。"""
    state = SyncState(tmp_path)
    fp = FileProjection(project_root=str(tmp_path), sync_state=state)
    layer = _layer(tmp_path, MockWorld([]), state, fp)
    report = layer.detect()
    assert report.status == "NOT_A_GIT_REPO", report.format()


def test_external_sync_receipt_is_not_forge_tool(tmp_path):
    """external_sync 是独立事实来源，不是 forge_tool 修改（契约 §6）。"""
    _init_git_repo(tmp_path)
    target = tmp_path / "file.txt"
    receipt = _file_receipt(1, str(target.resolve()), "v1\n", source="external_sync")

    state = SyncState(tmp_path)
    fp = FileProjection(project_root=str(tmp_path), sync_state=state)
    layer = _layer(tmp_path, MockWorld([receipt]), state, fp)

    # source=external_sync 的 receipt 不算 forge_tool 写盘推进
    report = layer.detect()
    assert report.status == IN_SYNC, report.format()
    assert state.disk_synced_version == 0


class MockUnavailableWorld:
    """模拟 veritasd 离线：所有 World 查询都抛异常。"""

    def get_receipts_since(self, version):
        raise RuntimeError("veritasd unavailable")

    def get_version(self):
        raise RuntimeError("veritasd unavailable")


def test_world_unavailable_is_not_in_sync(tmp_path):
    """veritasd 离线时，detect() 不得返回 IN_SYNC。

    World 不可用 ≠ World 没有新变化。当前实现可能把异常吞成
    空列表，误判为 IN_SYNC。此测试先钉死这个 bug。
    """
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.email", "t@t"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.name", "t"],
        check=True,
    )
    (tmp_path / "README.md").write_text("init\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-q", "-m", "init"], check=True)

    state = SyncState(tmp_path)
    fp = FileProjection(project_root=str(tmp_path), sync_state=state)
    layer = SyncLayer(str(tmp_path), MockUnavailableWorld(), state, fp)

    report = layer.detect()
    assert report.status != IN_SYNC, (
        f"veritasd 离线时不得返回 IN_SYNC，实际返回 {report.status}"
    )


def test_world_unavailable_forge_sync_does_not_advance(tmp_path):
    """veritasd 离线时 forge_sync 不得推进 disk_synced_version。"""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.email", "t@t"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "user.name", "t"],
        check=True,
    )
    (tmp_path / "README.md").write_text("init\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(tmp_path), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(tmp_path), "commit", "-q", "-m", "init"], check=True)

    state = SyncState(tmp_path)
    fp = FileProjection(project_root=str(tmp_path), sync_state=state)
    layer = SyncLayer(str(tmp_path), MockUnavailableWorld(), state, fp)

    report = layer.sync()
    assert report.status == WORLD_UNAVAILABLE
    assert state.disk_synced_version == 0

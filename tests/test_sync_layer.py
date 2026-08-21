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


# ── 缺口 #1：外部新建 untracked ─────────────────────────────────


def test_external_untracked_file_is_conflict(tmp_path):
    """外部新建、Forge 从未跟踪的文件 → CONFLICT(untracked_external)，不能 IN_SYNC。"""
    from forge.sync.sync_layer import CONFLICT_UNTRACKED_EXTERNAL

    _init_git_repo(tmp_path)
    target = tmp_path / "file.txt"
    receipt = _file_receipt(1, str(target.resolve()), "v1\n")

    state = SyncState(tmp_path)
    fp = FileProjection(project_root=str(tmp_path), sync_state=state)
    assert fp.apply(receipt, receipt.delta).success

    layer = _layer(tmp_path, MockWorld([receipt]), state, fp)
    assert layer.detect().status == IN_SYNC

    # 外部新建
    external = tmp_path / "external_new.txt"
    external.write_text("from user\n", encoding="utf-8")

    report = layer.detect()
    assert report.status == CONFLICT, report.format()
    assert report.conflict_kind == CONFLICT_UNTRACKED_EXTERNAL, report.format()
    assert any("external_new.txt" in p for p in report.divergent_paths), report.format()
    assert "从未跟踪" in report.detail or "untracked" in report.detail.lower()
    # 不推进水位
    assert state.disk_synced_version == 1


def test_forge_own_untracked_write_is_not_external_conflict(tmp_path):
    """Forge 刚写、尚未 git add 的 untracked 文件不得被误判为外部新建。"""
    _init_git_repo(tmp_path)
    target = tmp_path / "forge_only.txt"
    receipt = _file_receipt(1, str(target.resolve()), "forge\n")

    state = SyncState(tmp_path)
    fp = FileProjection(project_root=str(tmp_path), sync_state=state)
    assert fp.apply(receipt, receipt.delta).success
    # 仍在 last_known，且 git status 为 ??
    assert str(target.resolve()) in state.last_known_file_hashes

    layer = _layer(tmp_path, MockWorld([receipt]), state, fp)
    report = layer.detect()
    assert report.status == IN_SYNC, report.format()


def test_content_conflict_has_content_kind(tmp_path):
    """双方前进时 CONFLICT 的 conflict_kind 为 content_divergence。"""
    from forge.sync.sync_layer import CONFLICT_CONTENT

    _init_git_repo(tmp_path)
    target = tmp_path / "file.txt"
    r1 = _file_receipt(1, str(target.resolve()), "v1\n")
    r2 = _file_receipt(2, str(target.resolve()), "v2\n")

    state = SyncState(tmp_path)
    fp = FileProjection(project_root=str(tmp_path), sync_state=state)
    fp.apply(r1, r1.delta)

    # 外部改磁盘
    target.write_text("USER\n", encoding="utf-8")
    # World 另有未物化 receipt
    layer = _layer(tmp_path, MockWorld([r1, r2]), state, fp)
    report = layer.detect()
    assert report.status == CONFLICT, report.format()
    assert report.conflict_kind == CONFLICT_CONTENT, report.format()


# ── 缺口 #4b：批量写入中途外部改动 ─────────────────────────────


def _multi_file_receipt(version, path_contents: dict):
    """多个 object 的 full-content 写 receipt。"""
    memory = []
    oid = 1
    for abs_path, content in path_contents.items():
        path_hex = binascii.hexlify(str(abs_path).encode("utf-8")).decode("ascii")
        content_hex = binascii.hexlify(content.encode("utf-8")).decode("ascii")
        memory.append({"object_id": oid, "state_id": 0, "value_hex": path_hex})
        memory.append({"object_id": oid, "state_id": 1, "value_hex": content_hex})
        oid += 1
    delta = TransactionDelta(memory_written=memory)
    return Receipt(
        tx_id=version,
        before_root=0,
        after_root=version,
        version=version,
        delta=delta,
        source="forge_tool",
    )


def test_mid_batch_external_change_stops_and_rolls_back(tmp_path):
    """多文件 apply：写完 A 后外部改 B → 整批失败、A 回滚、不推进水位。"""
    _init_git_repo(tmp_path)
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    a.write_text("A0\n", encoding="utf-8")
    b.write_text("B0\n", encoding="utf-8")

    state = SyncState(tmp_path)
    # 先登记两个文件为 known，模拟已同步基线
    state.mark_disk_synced(
        version=1,
        written_paths=[str(a.resolve()), str(b.resolve())],
        deleted_paths=[],
    )
    assert state.disk_synced_version == 1

    receipt = _multi_file_receipt(
        2,
        {str(a.resolve()): "A1\n", str(b.resolve()): "B1\n"},
    )

    # 劫持 FileManager.write：第一次写成功后改 B 的内容，模拟外部并发
    fp = FileProjection(project_root=str(tmp_path), sync_state=state)
    real_write = fp.fm.write
    call_count = {"n": 0}

    def sneaky_write(path, content):
        call_count["n"] += 1
        real_write(path, content)
        if call_count["n"] == 1:
            # 第一个文件写完后，外部改第二个目标
            b.write_text("EXTERNAL\n", encoding="utf-8")

    fp.fm.write = sneaky_write

    res = fp.apply(receipt, receipt.delta)
    assert not res.success, res.reason
    assert "external change" in res.reason.lower() or "drift" in res.reason.lower()
    # 水位不得推进
    assert state.disk_synced_version == 1
    # A 应被回滚到 A0（有 backup）
    assert a.read_text(encoding="utf-8") == "A0\n"
    # B 仍是外部内容（我们尚未写 B 或写前已检测）
    assert b.read_text(encoding="utf-8") == "EXTERNAL\n"


def test_rollback_failure_marks_uncertain_and_forgets_known(tmp_path):
    """回滚失败时：uncertain_paths 列出，并从 last_known_file_hashes 移除。"""
    _init_git_repo(tmp_path)
    a = tmp_path / "a.txt"
    a.write_text("A0\n", encoding="utf-8")

    state = SyncState(tmp_path)
    state.mark_disk_synced(version=1, written_paths=[str(a.resolve())], deleted_paths=[])
    assert str(a.resolve()) in state.last_known_file_hashes

    receipt = _file_receipt(2, str(a.resolve()), "A1\n")
    fp = FileProjection(project_root=str(tmp_path), sync_state=state)

    # 写成功后强制触发校验失败路径：让 validate 失败，并让 restore_latest 失败
    real_write = fp.fm.write

    def write_then_break(path, content):
        real_write(path, content)

    fp.fm.write = write_then_break

    from forge.core import validator as val_mod

    # 使语法校验失败以进入回滚分支
    class _AlwaysFail:
        @staticmethod
        def validate(path):
            return False, "forced fail"

    # ValidatorRegistry.validate is used
    from forge.core.validator import ValidatorRegistry

    orig_validate = ValidatorRegistry.validate

    @classmethod
    def fail_validate(cls, path):
        return False, "forced fail"

    ValidatorRegistry.validate = fail_validate
    # 破坏 backup restore
    fp.backup.restore_latest = lambda target: False

    try:
        res = fp.apply(receipt, receipt.delta)
    finally:
        ValidatorRegistry.validate = orig_validate

    assert not res.success
    assert str(a.resolve()) in (res.uncertain_paths or []) or any(
        "uncertain" in (res.reason or "").lower() for _ in [0]
    )
    # 关键路径不得留在 known
    assert str(a.resolve()) not in state.last_known_file_hashes
    assert state.disk_synced_version == 1

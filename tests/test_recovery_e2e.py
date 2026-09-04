"""端到端崩溃恢复测试：recover() 与真实多路径 expected effect 格式对齐。

模拟「apply 成功、磁盘已写入、mark_disk_synced 未落」这一崩溃窗口，使用
真实的 SyncState / SyncLayer / FileProjection（无 MagicMock），验证 recover()
能按 FileProjection.prepare() 产出的真实多路径 expected effect 正确 backfill。

Expected-effect 格式（canonical，apply_world_to_disk_decision 写入、recover 读取）：

    {
        "written_paths": [{"path": str, "hash": sha256_hex}, ...],
        "deleted_paths": [str, ...],
    }
"""
from __future__ import annotations

import binascii
import hashlib

from forge.projections.file_projection import FileProjection
from forge.sync.attempt import ReconcileAttemptStore, recover
from forge.sync.git_utils import hash_file
from forge.sync.state import SyncState
from forge.sync.sync_layer import SyncLayer
from forge.world.types import Receipt, TransactionDelta


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


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
        tx_id=version, before_root=0, after_root=version,
        version=version, delta=delta, source="forge_tool",
    )


class _World:
    """真实（非 Mock）World 适配器：返回冻结的 receipt 序列。"""

    def __init__(self, receipts):
        self._receipts = list(receipts)

    def get_receipts_since(self, version):
        return [r for r in self._receipts if r.version > version]

    def get_version(self):
        return max((r.version for r in self._receipts), default=0)


def _write_expected_effect(store, attempt, index, projection, receipt):
    """镜像 apply_world_to_disk_decision 的「apply 前 durable 写 expected effect」。"""
    info = projection.prepare(receipt.delta) or {}
    written = list(info.get("files_modified", []) or []) + list(
        info.get("files_created", []) or []
    )
    deleted = list(info.get("files_deleted", []) or [])
    target_hashes = info.get("target_hashes", {}) or {}
    effect = {
        "written_paths": [
            {"path": p, "hash": target_hashes.get(p)} for p in written
        ],
        "deleted_paths": deleted,
    }
    store.set_expected_effect(attempt, index, effect=effect)
    return written, deleted


def test_recover_backfills_crash_after_apply_before_mark(tmp_path):
    """apply 已写盘、mark 未落 → recover 判定 backfilled，随后回填水位。"""
    root = tmp_path / "repo"
    root.mkdir(parents=True)
    target = root / "hello.txt"

    state = SyncState(root)
    # sync_state=None：FileProjection.apply() 只写盘、不推进水位，
    # 精确复现「磁盘已写入但 mark_disk_synced 未落」的崩溃窗口。
    fp = FileProjection(project_root=str(root), sync_state=None)
    receipt = _file_receipt(1, str(target.resolve()), "hello world\n")

    # 真实 SyncLayer 接线（World 为真实适配器，非 MagicMock）。
    layer = SyncLayer(
        str(root),
        world_runtime=_World([receipt]),
        sync_state=state,
        file_projection=fp,
    )
    assert layer.state is state
    assert layer._file_projection is fp

    store = ReconcileAttemptStore(root / ".forge")
    attempt = store.create(
        {"decision_id": "dec-1", "generation": {"world_version": 1}},
        [receipt],
    )
    written, deleted = _write_expected_effect(store, attempt, 0, fp, receipt)

    # ── 崩溃点：apply 已执行、写盘成功，mark_disk_synced 未执行 ──
    result = fp.apply(receipt, receipt.delta)
    assert result.success
    assert target.exists()
    assert state.disk_synced_version == 0  # mark 确实没落

    rr = recover(store, root)
    assert rr.action == "backfilled_and_ready"
    assert rr.reason is None

    # 调用方（sync_layer）在 backfilled_and_ready 上执行回填：
    state.mark_disk_synced(receipt.version, written, deleted, source="forge_tool")
    assert state.disk_synced_version == 1
    assert state.last_known_file_hashes[str(target.resolve())] == hash_file(
        str(target.resolve())
    )

    # 回填后收尾 attempt。
    store.record_progress(attempt, next_receipt_index=1, last_marked_version=receipt.version)
    store.mark_completed(store.load())
    assert store.load().status == "COMPLETED"


def test_recover_stops_on_content_mismatch(tmp_path):
    """写盘内容与 expected hash 不符 → stopped，给出 mismatch 详情。"""
    root = tmp_path / "repo"
    root.mkdir(parents=True)
    target = root / "hello.txt"

    state = SyncState(root)
    fp = FileProjection(project_root=str(root), sync_state=None)
    receipt = _file_receipt(1, str(target.resolve()), "hello world\n")

    store = ReconcileAttemptStore(root / ".forge")
    attempt = store.create(
        {"decision_id": "dec-1", "generation": {"world_version": 1}},
        [receipt],
    )
    _write_expected_effect(store, attempt, 0, fp, receipt)

    assert fp.apply(receipt, receipt.delta).success
    # 模拟写盘后内容被外部/崩溃破坏：内容不等于 apply 应写出的内容。
    target.write_text("HELLO WORLD\n", encoding="utf-8")

    rr = recover(store, root)
    assert rr.action == "stopped"
    assert rr.mismatched_index == 0
    assert rr.mismatched_path == str(target.resolve())
    assert rr.expected == _sha(b"hello world\n")
    assert rr.actual == _sha(b"HELLO WORLD\n")

    # attempt 原地不动：仍 IN_PROGRESS、边界不变。
    reloaded = store.load()
    assert reloaded.status == "IN_PROGRESS"
    assert reloaded.next_receipt_index == 0


def test_recover_backfills_multi_path_write_and_delete(tmp_path):
    """单笔 receipt 同时写新文件 + 删旧文件：多路径 expected effect 全匹配 → backfill。"""
    root = tmp_path / "repo"
    root.mkdir(parents=True)
    new_file = root / "new.txt"
    old_file = root / "old.txt"
    old_file.write_text("to be deleted\n", encoding="utf-8")

    delta = TransactionDelta(
        memory_written=[
            {
                "object_id": 1,
                "state_id": 0,
                "value_hex": binascii.hexlify(
                    str(new_file.resolve()).encode("utf-8")
                ).decode("ascii"),
            },
            {
                "object_id": 1,
                "state_id": 1,
                "value_hex": binascii.hexlify(b"fresh\n").decode("ascii"),
            },
        ],
        objects_deleted=[2],
        metadata={"deleted_paths": {2: str(old_file.resolve())}},
    )
    receipt = Receipt(
        tx_id=1, before_root=0, after_root=1, version=1, delta=delta, source="forge_tool"
    )

    state = SyncState(root)
    fp = FileProjection(project_root=str(root), sync_state=None)

    store = ReconcileAttemptStore(root / ".forge")
    attempt = store.create(
        {"decision_id": "dec-1", "generation": {"world_version": 1}},
        [receipt],
    )
    written, deleted = _write_expected_effect(store, attempt, 0, fp, receipt)
    # 期望效应确实覆盖了两个路径（写 + 删），而非单路径格式。
    assert written == [str(new_file.resolve())]
    assert deleted == [str(old_file.resolve())]

    # 崩溃窗口：apply 写盘 + 删盘，mark 未落。
    result = fp.apply(receipt, receipt.delta)
    assert result.success
    assert new_file.exists()
    assert not old_file.exists()
    assert state.disk_synced_version == 0

    rr = recover(store, root)
    assert rr.action == "backfilled_and_ready"

    state.mark_disk_synced(receipt.version, written, deleted, source="forge_tool")
    assert state.disk_synced_version == 1
    assert str(new_file.resolve()) in state.last_known_file_hashes
    assert str(old_file.resolve()) not in state.last_known_file_hashes


if __name__ == "__main__":
    import sys

    sys.exit("run with pytest")

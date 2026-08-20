"""recovery 分叉时保留磁盘，不覆盖手动修改。"""
from __future__ import annotations

import binascii
import os
from pathlib import Path

from forge.projections.file_projection import FileProjection
from forge.world.types import Receipt, TransactionDelta


def _make_receipt(version: int, abs_path: str, content: str) -> Receipt:
    path_hex = binascii.hexlify(abs_path.encode("utf-8")).decode("ascii")
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
    )


def test_preserve_diverged_disk(tmp_path):
    """磁盘已被手动改过时，recovery 不得用 World 旧内容覆盖。"""
    root = tmp_path / "proj"
    root.mkdir()
    target = root / "local_tools.py"
    manual = "# MANUAL PATCH\npost_toot = True\n"
    target.write_text(manual, encoding="utf-8")

    world_old = "# OLD FROM WORLD\n"
    receipt = _make_receipt(1, str(target.resolve()), world_old)

    fp = FileProjection(project_root=str(root))
    fp.recovery_preserve_disk = True
    result = fp.apply(receipt, receipt.delta)
    assert result.success, result.reason
    assert target.read_text(encoding="utf-8") == manual
    assert str(target.resolve()) in fp.last_skipped_diverged or any(
        "local_tools.py" in p for p in fp.last_skipped_diverged
    )


def test_missing_file_still_restored(tmp_path):
    """磁盘上不存在的文件仍应从 World 恢复。"""
    root = tmp_path / "proj"
    root.mkdir()
    target = root / "missing.py"
    assert not target.exists()

    content = "print('restored')\n"
    receipt = _make_receipt(1, str(target.resolve()), content)

    fp = FileProjection(project_root=str(root))
    fp.recovery_preserve_disk = True
    result = fp.apply(receipt, receipt.delta)
    assert result.success, result.reason
    assert target.read_text(encoding="utf-8") == content


def test_identical_content_noop(tmp_path):
    """内容一致时不必跳过列表有值（写入相同内容也可）。"""
    root = tmp_path / "proj"
    root.mkdir()
    target = root / "same.py"
    content = "x = 1\n"
    target.write_text(content, encoding="utf-8")

    receipt = _make_receipt(1, str(target.resolve()), content)
    fp = FileProjection(project_root=str(root))
    fp.recovery_preserve_disk = True
    result = fp.apply(receipt, receipt.delta)
    assert result.success
    assert target.read_text(encoding="utf-8") == content


def test_legacy_recovery_tests():
    from tests.test_recovery import test_normal_recovery, test_crash_recovery
    test_normal_recovery()
    test_crash_recovery()

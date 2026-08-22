"""Regression: FileProjection must not write/delete after read/backup failure."""
from __future__ import annotations

from unittest.mock import patch

from forge.projections.base import ProjectionResult, TransactionDelta
from forge.projections.file_projection import FileProjection
from forge.world.types import Receipt


def _receipt(version: int = 1) -> Receipt:
    return Receipt(
        tx_id=1,
        version=version,
        before_root=0,
        after_root=1,
        source="test",
    )


def _delta_write(path: str, content: str) -> TransactionDelta:
    """Minimal delta: one object with path (sid=0) + full content (sid=1)."""
    path_hex = path.encode("utf-8").hex()
    content_hex = content.encode("utf-8").hex()
    return TransactionDelta(
        memory_written=[
            {"object_id": 1001, "state_id": 0, "value_hex": path_hex},
            {"object_id": 1001, "state_id": 1, "value_hex": content_hex},
        ],
    )


def _delta_delete(path: str, object_id: int = 1001) -> TransactionDelta:
    return TransactionDelta(
        objects_deleted=[object_id],
        metadata={"deleted_paths": {object_id: path}},
    )


def test_read_existing_file_failure_aborts_without_overwrite(tmp_path):
    """Case A: existing file cannot be read → fail, target content unchanged."""
    root = tmp_path
    target = root / "existing.txt"
    target.write_text("ORIGINAL_CONTENT\n", encoding="utf-8")

    fp = FileProjection(project_root=str(root))
    delta = _delta_write(str(target), "NEW_SHOULD_NOT_LAND\n")
    receipt = _receipt()

    with patch.object(fp.fm, "read", side_effect=OSError("simulated read failure")):
        result = fp.apply(receipt, delta)

    assert isinstance(result, ProjectionResult)
    assert result.success is False
    assert "cannot read existing file" in (result.reason or "")
    assert target.read_text(encoding="utf-8") == "ORIGINAL_CONTENT\n"


def test_backup_failure_before_write_preserves_file(tmp_path):
    """Case B: backup fails → fail, original file content unchanged."""
    root = tmp_path
    target = root / "existing.txt"
    target.write_text("KEEP_ME\n", encoding="utf-8")

    fp = FileProjection(project_root=str(root))
    delta = _delta_write(str(target), "OVERWRITE\n")
    receipt = _receipt()

    with patch.object(fp.backup, "backup", side_effect=OSError("backup disk full")):
        result = fp.apply(receipt, delta)

    assert result.success is False
    assert "backup failed" in (result.reason or "")
    assert "before write" in (result.reason or "")
    assert target.read_text(encoding="utf-8") == "KEEP_ME\n"


def test_backup_failure_before_delete_preserves_file(tmp_path):
    """Case C: backup fails before delete → fail, file still exists."""
    root = tmp_path
    target = root / "to_delete.txt"
    target.write_text("STILL_HERE\n", encoding="utf-8")

    fp = FileProjection(project_root=str(root))
    delta = _delta_delete(str(target), object_id=42)
    receipt = _receipt()

    with patch.object(fp.backup, "backup", side_effect=OSError("backup denied")):
        result = fp.apply(receipt, delta)

    assert result.success is False
    assert "backup failed" in (result.reason or "")
    assert "before delete" in (result.reason or "")
    assert target.exists()
    assert target.read_text(encoding="utf-8") == "STILL_HERE\n"


def test_normal_modify_and_delete_still_work(tmp_path):
    """Case D: happy path modify + delete still succeed."""
    root = tmp_path
    target = root / "ok.txt"
    target.write_text("old\n", encoding="utf-8")

    fp = FileProjection(project_root=str(root))

    delta_w = _delta_write(str(target), "new\n")
    r1 = fp.apply(_receipt(1), delta_w)
    assert r1.success is True, r1.reason
    assert target.read_text(encoding="utf-8") == "new\n"

    delta_d = _delta_delete(str(target), object_id=7)
    r2 = fp.apply(_receipt(2), delta_d)
    assert r2.success is True, r2.reason
    assert not target.exists()


def test_create_new_file_does_not_require_read_or_backup(tmp_path):
    """Create path: file does not exist → no read/backup required."""
    root = tmp_path
    target = root / "brand_new.txt"
    assert not target.exists()

    fp = FileProjection(project_root=str(root))
    delta = _delta_write(str(target), "created\n")
    result = fp.apply(_receipt(), delta)
    assert result.success is True, result.reason
    assert target.read_text(encoding="utf-8") == "created\n"

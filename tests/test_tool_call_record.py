"""Tests for forge.tool_call_record — append-only ToolCallRecord log."""
from __future__ import annotations

import os
from pathlib import Path

from forge.tool_call_record import (
    ToolCallRecord,
    current_timestamp,
    get_record,
    write_record,
)


def _sample_record(tool_call_id: str = "tc_test_1", subtask_id: str = "sub_1") -> ToolCallRecord:
    return ToolCallRecord(
        tool_call_id=tool_call_id,
        subtask_id=subtask_id,
        tool_name="search_code",
        input={"pattern": "x"},
        output={"hits": 1},
        status="success",
        error=None,
        timestamp=current_timestamp(),
    )


def test_write_then_get_record(tmp_path: Path):
    rec = _sample_record()
    assert write_record(tmp_path, rec) is True
    found = get_record(tmp_path, "tc_test_1")
    assert found is not None
    assert found["tool_call_id"] == "tc_test_1"
    assert found["tool_name"] == "search_code"
    assert found["subtask_id"] == "sub_1"
    assert found["status"] == "success"
    assert found["input"] == {"pattern": "x"}


def test_get_record_missing_id_returns_none(tmp_path: Path):
    assert get_record(tmp_path, "tc_does_not_exist") is None
    # after writing another id, missing still None
    assert write_record(tmp_path, _sample_record("tc_other")) is True
    assert get_record(tmp_path, "tc_does_not_exist") is None
    assert get_record(tmp_path, "tc_other") is not None


def test_write_record_failure_returns_false_no_raise(tmp_path: Path):
    """Logging failure must not raise — caller sees False only."""
    # Point project_root at a regular file so mkdir/open under it fails.
    blocker = tmp_path / "not_a_dir"
    blocker.write_text("x", encoding="utf-8")
    # Path becomes not_a_dir/.forge/... which cannot be created
    ok = write_record(blocker, _sample_record())
    assert ok is False
    assert get_record(blocker, "tc_test_1") is None

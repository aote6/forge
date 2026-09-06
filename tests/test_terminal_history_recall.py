"""Terminal history recall: ToolCallRecord.display + last <tool_call_id>."""
from __future__ import annotations

import json
from pathlib import Path

from forge.terminal_present import (
    MIN_OMIT_LINES,
    summarize_tool_display,
)
from forge.tool_call_record import (
    ToolCallRecord,
    current_timestamp,
    get_latest_record_with_display,
    get_record,
    write_record,
)


def test_record_display_roundtrip(tmp_path: Path):
    rec = ToolCallRecord(
        tool_call_id="tc_disp_1",
        subtask_id="",
        tool_name="read_file",
        input={"path": "a.py"},
        output={"mutation": False},
        status="success",
        error=None,
        timestamp=current_timestamp(),
        actor="main",
        display="line1\nline2\nline3",
    )
    assert write_record(tmp_path, rec) is True
    found = get_record(tmp_path, "tc_disp_1")
    assert found is not None
    assert found["display"] == "line1\nline2\nline3"
    assert found["output"] == {"mutation": False}
    assert found["tool_name"] == "read_file"


def test_legacy_jsonl_without_display_loads(tmp_path: Path):
    path = tmp_path / ".forge" / "tool_call_records.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    legacy = {
        "tool_call_id": "tc_old",
        "subtask_id": "",
        "tool_name": "read_file",
        "input": {},
        "output": None,
        "status": "success",
        "error": None,
        "timestamp": 1.0,
        "actor": "main",
    }
    path.write_text(json.dumps(legacy) + "\n", encoding="utf-8")
    found = get_record(tmp_path, "tc_old")
    assert found is not None
    assert found["display"] is None
    assert found["tool_call_id"] == "tc_old"


def test_historical_last_not_overwritten_by_later_records(tmp_path: Path):
    for i, text in enumerate(["AAA-full", "BBB-full", "CCC-full"], start=1):
        write_record(
            tmp_path,
            ToolCallRecord(
                tool_call_id=f"tc_{i}",
                subtask_id="",
                tool_name="run_command",
                input={},
                output=None,
                status="success",
                error=None,
                timestamp=float(i),
                actor="main",
                display=text,
            ),
        )
    assert get_record(tmp_path, "tc_1")["display"] == "AAA-full"
    assert get_record(tmp_path, "tc_2")["display"] == "BBB-full"
    assert get_record(tmp_path, "tc_3")["display"] == "CCC-full"
    latest = get_latest_record_with_display(tmp_path)
    assert latest is not None
    assert latest["tool_call_id"] == "tc_3"
    assert latest["display"] == "CCC-full"


def test_omit_threshold_success_under_min_shows_full():
    # HEAD=4 TAIL=12 → omit = n - 16; n=17 → omit 1; n=20 → omit 4; n=21 → omit 5
    for n, should_fold in ((17, False), (20, False), (21, True)):
        lines = [f"L{i:03d}" for i in range(n)]
        text = "\n".join(lines)
        out = summarize_tool_display(
            text, success=True, tool_call_id="tc_x", tool_name="read_file"
        )
        if should_fold:
            assert "省略" in out
            assert "tc_x" in out
            assert "last tc_x" in out
            assert "read_file" in out
        else:
            assert out == text
            assert "省略" not in out


def test_omit_threshold_failure_under_min_shows_full():
    # failure keeps last 16 lines; n=17 → omit 1; n=20 → omit 4; n=21 → omit 5
    for n, should_fold in ((17, False), (20, False), (21, True)):
        lines = [f"E{i:03d}" for i in range(n)]
        text = "\n".join(lines)
        out = summarize_tool_display(
            text, success=False, tool_call_id="tc_fail", tool_name="run_command"
        )
        if should_fold:
            assert "省略" in out
            assert "tc_fail" in out
            assert "last tc_fail" in out
        else:
            assert out == text


def test_min_omit_constant():
    assert MIN_OMIT_LINES == 5


def test_record_main_helper_stores_display(tmp_path: Path):
    from forge.adapters.base import ToolResult
    from forge.runtime import _record_main_tool_call

    result = ToolResult.ok(display="hello\nworld", payload={"k": 1})
    tc_id, ok = _record_main_tool_call(
        str(tmp_path),
        tool_name="read_file",
        arguments={"path": "x"},
        result=result,
    )
    assert ok is True
    assert tc_id and tc_id.startswith("tc_")
    rec = get_record(tmp_path, tc_id)
    assert rec["display"] == "hello\nworld"
    assert rec["output"] == {"k": 1}
    assert rec["actor"] == "main"


def test_remember_tool_end_event_matches_record(tmp_path: Path):
    from forge.adapters.base import ToolResult
    from forge.events import EventType
    from forge.runtime import Runtime, _record_main_tool_call
    from forge.workspace import Workspace

    # Minimal Runtime is heavy; test helper + event payload shape via a stub.
    class _Stub:
        def __init__(self):
            self.events = []
            self._last_tool_call_id = None
            self._last_tool_display = ""
            self._last_tool_name = ""

        def emit(self, event):
            self.events.append(event)

    from forge.runtime import Runtime as RT

    stub = _Stub()
    # bind unbound method
    result = ToolResult.ok(display="BODY_TEXT", payload={})
    tc_id, ok = _record_main_tool_call(
        str(tmp_path), tool_name="glob_files", arguments={}, result=result
    )
    assert ok
    RT._remember_tool_end(stub, name="glob_files", result=result, tool_call_id=tc_id)
    assert stub._last_tool_call_id == tc_id
    assert stub._last_tool_display == "BODY_TEXT"
    assert len(stub.events) == 1
    data = stub.events[0].data
    assert data["tool_call_id"] == tc_id
    assert data["display"] == "BODY_TEXT"
    assert data["name"] == "glob_files"
    rec = get_record(tmp_path, tc_id)
    assert rec["display"] == data["display"]
    assert rec["tool_call_id"] == data["tool_call_id"]


def test_subagent_record_includes_display(tmp_path: Path):
    from forge.adapters.base import ToolCall, ToolResult
    from forge.subagent import _execute_tool

    def fake_tool(**kwargs):
        return ToolResult.ok(display="SUB_OUT", payload={"ok": True})

    tools = {"ping": fake_tool}
    tc = ToolCall(id="m1", name="ping", arguments={})
    records = []
    result, tc_id = _execute_tool(
        tools, tc, project_root=tmp_path, subtask_id="sub_x", records_out=records
    )
    assert result.success
    assert tc_id and tc_id.startswith("tc_")
    assert records[0].display == "SUB_OUT"
    loaded = get_record(tmp_path, tc_id)
    assert loaded["display"] == "SUB_OUT"
    assert loaded["actor"] == "subagent"

"""Tests for verify_tool_call readonly tool."""
from __future__ import annotations

from pathlib import Path

from forge.tool_call_record import ToolCallRecord, current_timestamp, write_record
from forge.tools.meta_tools import make_meta_tools
from forge.tools.schemas import READ_ONLY_TOOL_DECLARATIONS
from forge.workspace import Workspace


def test_verify_tool_call_on_schema():
    names = {d["name"] for d in READ_ONLY_TOOL_DECLARATIONS}
    assert "verify_tool_call" in names
    decl = next(d for d in READ_ONLY_TOOL_DECLARATIONS if d["name"] == "verify_tool_call")
    assert "tool_call_id" in decl["parameters"]["properties"]
    assert "tool_call_id" in decl["parameters"]["required"]


def test_verify_found_returns_record_fields_not_claim(tmp_path: Path):
    write_record(
        tmp_path,
        ToolCallRecord(
            tool_call_id="tc_v1",
            subtask_id="sub_9",
            tool_name="search_code",
            input={"pattern": "abc"},
            output={"n": 1},
            status="success",
            error=None,
            timestamp=current_timestamp(),
        ),
    )
    tools = make_meta_tools(Workspace(project_root=str(tmp_path)))
    r = tools["verify_tool_call"]("tc_v1")
    assert r.success
    assert r.payload["found"] is True
    assert r.payload["tool_name"] == "search_code"
    assert r.payload["input"] == {"pattern": "abc"}
    assert r.payload["output"] == {"n": 1}
    assert r.payload["status"] == "success"
    assert r.payload["error"] is None
    assert r.payload["subtask_id"] == "sub_9"
    assert "claim" not in r.payload
    assert "conclusion" not in r.payload


def test_verify_missing_fails(tmp_path: Path):
    tools = make_meta_tools(Workspace(project_root=str(tmp_path)))
    r = tools["verify_tool_call"]("tc_absent")
    assert r.success is False
    assert r.payload.get("found") is False


def test_verify_empty_id_fails(tmp_path: Path):
    tools = make_meta_tools(Workspace(project_root=str(tmp_path)))
    r = tools["verify_tool_call"]("")
    assert r.success is False
    r2 = tools["verify_tool_call"]("   ")
    assert r2.success is False

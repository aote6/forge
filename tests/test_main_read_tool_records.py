"""Main AI READ_ONLY + ToolCallRecord(actor=main) — P1 fact acquisition."""
from __future__ import annotations

from pathlib import Path

from forge.agent_abi import (
    AgentResult,
    Evidence,
    STATUS_DONE,
    lookup_evidence_records,
    verify_evidence,
)
from forge.tool_call_record import (
    ToolCallRecord,
    current_timestamp,
    get_record,
    new_tool_call_id,
    write_record,
)
from forge.tools.schemas import MAIN_READ_ONLY_TOOL_NAMES, MUTATION_TOOL_NAMES
from forge.runtime import _default_tool_schemas, _main_tool_policy_denied


def test_main_schemas_include_read_exclude_mutation():
    names = {d["name"] for d in _default_tool_schemas()}
    assert "read_file" in names
    assert "search_code" in names
    assert "spawn_subagent" in names
    for m in ("write_file", "str_replace", "run_command"):
        assert m not in names


def test_main_policy_denies_mutation_allows_read():
    assert _main_tool_policy_denied("read_file") is None
    assert _main_tool_policy_denied("search_code") is None
    assert _main_tool_policy_denied("spawn_subagent") is None
    assert _main_tool_policy_denied("write_file") is not None
    assert _main_tool_policy_denied("str_replace") is not None
    assert _main_tool_policy_denied("run_command") is not None


def test_legacy_jsonl_without_actor_defaults_subagent(tmp_path: Path):
    path = tmp_path / ".forge" / "tool_call_records.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    tc = "tc_legacy_no_actor_001"
    line = (
        '{"tool_call_id": "%s", "subtask_id": "sub_1", "tool_name": "search_code", '
        '"input": {}, "output": null, "status": "success", "error": null, '
        '"timestamp": 1.0}\n' % tc
    )
    path.write_text(line, encoding="utf-8")
    found = get_record(tmp_path, tc)
    assert found is not None
    assert found.get("actor") == "subagent"


def test_main_record_write_and_reload(tmp_path: Path):
    tc = new_tool_call_id()
    rec = ToolCallRecord(
        tool_call_id=tc,
        subtask_id="",
        tool_name="read_file",
        input={"path": "foo.py"},
        output={"text": "hello"},
        status="success",
        error=None,
        timestamp=current_timestamp(),
        actor="main",
    )
    assert write_record(tmp_path, rec) is True
    found = get_record(tmp_path, tc)
    assert found is not None
    assert found["actor"] == "main"
    assert found["subtask_id"] == ""
    assert found["tool_name"] == "read_file"
    assert found["output"] == {"text": "hello"}


def test_sub_evidence_rejects_main_record(tmp_path: Path):
    sid = "sub_ev_1"
    main_tc = new_tool_call_id()
    main_rec = ToolCallRecord(
        tool_call_id=main_tc,
        subtask_id="",
        tool_name="read_file",
        input={"path": "a.py"},
        output={"ok": True},
        status="success",
        error=None,
        timestamp=current_timestamp(),
        actor="main",
    )
    write_record(tmp_path, main_rec)
    items = [{"tool_call_id": main_tc, "claim": "I read it", "path": "a.py"}]
    assert verify_evidence(items, [main_rec], sid) == []
    ar = AgentResult(
        subtask_id=sid,
        status=STATUS_DONE,
        conclusion="x",
        evidence=(Evidence(tool_call_id=main_tc, claim="I read it"),),
        uncertain="",
        next="",
        stop_when_met=True,
        status_reason="ok",
    )
    looked = lookup_evidence_records(tmp_path, ar)
    assert looked[0]["ok"] is False


def test_verify_evidence_rejects_wrong_subtask():
    sid = "sub_ok"
    tc = "tc_" + "f" * 32
    items = [{"tool_call_id": tc, "claim": "x"}]
    records = [
        ToolCallRecord(
            tool_call_id=tc,
            subtask_id="sub_other",
            tool_name="read_file",
            input={},
            output=None,
            status="success",
            error=None,
            timestamp=1.0,
            actor="subagent",
        )
    ]
    assert verify_evidence(items, records, sid) == []


def test_main_read_only_names_are_subset_of_read_only():
    from forge.tools.schemas import READ_ONLY_TOOL_DECLARATIONS

    read_names = {d["name"] for d in READ_ONLY_TOOL_DECLARATIONS}
    assert MAIN_READ_ONLY_TOOL_NAMES <= read_names
    assert MAIN_READ_ONLY_TOOL_NAMES.isdisjoint(MUTATION_TOOL_NAMES)

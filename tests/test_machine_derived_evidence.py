"""MDE v1 tests — lock behavior before implementation.

Do NOT modify forge/*.py to make these pass until Step 3.
"""
import json
from pathlib import Path

import pytest

from forge.agent_abi import (
    AgentTask,
    CandidateResult,
    Evidence,
    assemble_agent_result,
    project_machine_evidence,
)
from forge.tool_call_record import ToolCallRecord


def _record(tool_call_id="tc_1", subtask_id="sub_x", tool_name="forge_sync",
            status="success", actor="subagent", input_data=None):
    return ToolCallRecord(
        tool_call_id=tool_call_id,
        subtask_id=subtask_id,
        tool_name=tool_name,
        input=input_data or {},
        output={"status": "IN_SYNC"},
        status=status,
        error=None if status == "success" else "boom",
        timestamp=1234567890.0,
        actor=actor,
    )


def _candidate(evidence_items=None, stop_when_met=True, exit_kind="stop_when"):
    return CandidateResult(
        conclusion="ok",
        evidence_items=evidence_items or [],
        uncertain="",
        next="",
        stop_when_met=stop_when_met,
        exit_kind=exit_kind,
        error_message="",
    )


def _task():
    return AgentTask(goal="sync", done_when="IN_SYNC", stop_when="CONFLICT")


# A. Projection unit tests

def test_success_record_projects_to_evidence():
    rec = _record()
    ev = project_machine_evidence([rec], "sub_x")
    assert len(ev) == 1
    assert ev[0].tool_call_id == "tc_1"
    assert ev[0].claim == "forge_sync 执行成功"
    assert ev[0].path is None
    assert ev[0].quote is None


def test_error_record_does_not_project():
    rec = _record(status="error")
    ev = project_machine_evidence([rec], "sub_x")
    assert ev == ()


def test_main_actor_record_does_not_project():
    rec = _record(actor="main")
    ev = project_machine_evidence([rec], "sub_x")
    assert ev == ()


def test_cross_subtask_record_does_not_project():
    rec = _record(subtask_id="sub_y")
    ev = project_machine_evidence([rec], "sub_x")
    assert ev == ()


# B. AgentResult integration behavior

def test_missing_model_evidence_still_has_machine_evidence():
    rec = _record()
    cand = _candidate(evidence_items=[])  # model did not write EVIDENCE text
    result = assemble_agent_result(_task(), cand, [rec], subtask_id="sub_x")
    assert [e.tool_call_id for e in result.evidence] == ["tc_1"]


def test_invalid_model_evidence_does_not_affect_authoritative():
    rec = _record(tool_call_id="tc_real")
    cand = _candidate(evidence_items=[
        {"tool_call_id": "tc_fake", "claim": "model hallucinated"}
    ])
    result = assemble_agent_result(_task(), cand, [rec], subtask_id="sub_x")
    assert [e.tool_call_id for e in result.evidence] == ["tc_real"]
    assert list(result.model_reported_evidence) == cand.evidence_items


# C. Persistence

def test_model_reported_evidence_persisted(tmp_path: Path):
    rec = _record()
    cand = _candidate(evidence_items=[
        {"tool_call_id": "tc_1", "claim": "model said this"}
    ])
    result = assemble_agent_result(_task(), cand, [rec], subtask_id="sub_x")

    d = tmp_path / ".forge"
    d.mkdir()
    p = d / "subagent_results.jsonl"
    p.write_text(json.dumps(result.to_dict(), ensure_ascii=False) + "\n")

    loaded = json.loads(p.read_text().splitlines()[0])
    assert loaded["model_reported_evidence"] == cand.evidence_items


def test_old_jsonl_without_model_reported_evidence_still_reads(tmp_path: Path):
    d = tmp_path / ".forge"
    d.mkdir()
    p = d / "subagent_results.jsonl"
    old_record = {
        "subtask_id": "sub_old",
        "status": "done",
        "conclusion": "old",
        "evidence": [{"tool_call_id": "tc_old", "claim": "old"}],
        "uncertain": "",
        "next": "",
        "stop_when_met": True,
        "status_reason": "ok",
        "raw_conclusion": "",
    }
    p.write_text(json.dumps(old_record, ensure_ascii=False) + "\n")

    from forge.subagent_results_store import load_subagent_results
    loaded = load_subagent_results(str(tmp_path))
    assert loaded["sub_old"]["evidence"][0]["tool_call_id"] == "tc_old"
    assert "model_reported_evidence" not in loaded["sub_old"]

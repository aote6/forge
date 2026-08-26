"""Tests for forge.agent_abi — AgentResult assembly and precheck."""
from __future__ import annotations

import pytest
from pathlib import Path

from forge.agent_abi import (
    STATUS_BLOCKED,
    STATUS_DONE,
    STATUS_NEED_DECISION,
    AgentResult,
    AgentTask,
    CandidateResult,
    Evidence,
    assemble_agent_result,
    done_when_satisfied_v1,
    precheck_agent_result,
)
from forge.tool_call_record import ToolCallRecord, current_timestamp, write_record


def _rec(tc_id: str, subtask_id: str = "sub_1") -> ToolCallRecord:
    return ToolCallRecord(
        tool_call_id=tc_id,
        subtask_id=subtask_id,
        tool_name="search_code",
        input={},
        output=None,
        status="success",
        error=None,
        timestamp=current_timestamp(),
    )


def test_status_rejects_invalid_enum():
    with pytest.raises(ValueError):
        AgentResult(
            subtask_id="s",
            status="incomplete",
            conclusion="",
            evidence=(),
            uncertain="",
            next="",
            stop_when_met=False,
            status_reason="",
        )


def test_assemble_done_without_evidence_becomes_blocked():
    task = AgentTask(goal="g", subtask_id="sub_1")
    cand = CandidateResult(
        conclusion="ok",
        evidence_items=[],
        stop_when_met=True,
        exit_kind="stop_when",
    )
    r = assemble_agent_result(task, cand, [_rec("tc_1")], subtask_id="sub_1")
    assert r.status == STATUS_BLOCKED


def test_evidence_without_tool_call_id_stripped():
    task = AgentTask(goal="g", subtask_id="sub_1")
    cand = CandidateResult(
        conclusion="ok",
        evidence_items=[{"tool_call_id": "", "claim": "no id"}],
        stop_when_met=True,
        exit_kind="stop_when",
    )
    r = assemble_agent_result(task, cand, [_rec("tc_1")], subtask_id="sub_1")
    assert r.evidence == ()
    assert r.status == STATUS_BLOCKED


def test_evidence_cross_subtask_stripped():
    task = AgentTask(goal="g", subtask_id="sub_1")
    cand = CandidateResult(
        conclusion="ok",
        evidence_items=[{"tool_call_id": "tc_other", "claim": "x"}],
        stop_when_met=True,
        exit_kind="stop_when",
    )
    records = [_rec("tc_other", subtask_id="sub_OTHER")]
    r = assemble_agent_result(task, cand, records, subtask_id="sub_1")
    assert r.evidence == ()
    assert r.status == STATUS_BLOCKED


def test_done_when_v1_proxy():
    assert done_when_satisfied_v1(True, [Evidence(tool_call_id="tc")]) is True
    assert done_when_satisfied_v1(True, []) is False
    assert done_when_satisfied_v1(False, [Evidence(tool_call_id="tc")]) is False


def test_assemble_done_with_valid_evidence():
    task = AgentTask(goal="g", subtask_id="sub_1")
    cand = CandidateResult(
        conclusion="found",
        evidence_items=[{"tool_call_id": "tc_1", "claim": "hit", "path": "a.py"}],
        stop_when_met=True,
        exit_kind="stop_when",
    )
    r = assemble_agent_result(task, cand, [_rec("tc_1")], subtask_id="sub_1")
    assert r.status == STATUS_DONE
    assert len(r.evidence) == 1


def test_precheck_done_all_valid_stays_done(tmp_path: Path):
    write_record(tmp_path, _rec("tc_a"))
    ar = AgentResult(
        subtask_id="sub_1",
        status=STATUS_DONE,
        conclusion="c",
        evidence=(Evidence(tool_call_id="tc_a", claim="x"),),
        uncertain="",
        next="",
        stop_when_met=True,
        status_reason="ok",
    )
    out = precheck_agent_result(tmp_path, ar)
    assert out.status == STATUS_DONE
    assert len(out.evidence) == 1


def test_precheck_done_partial_invalid_keeps_done_strips_bad(tmp_path: Path):
    write_record(tmp_path, _rec("tc_good"))
    ar = AgentResult(
        subtask_id="sub_1",
        status=STATUS_DONE,
        conclusion="c",
        evidence=(
            Evidence(tool_call_id="tc_good", claim="ok"),
            Evidence(tool_call_id="tc_bad", claim="no"),
        ),
        uncertain="",
        next="",
        stop_when_met=True,
        status_reason="ok",
    )
    out = precheck_agent_result(tmp_path, ar)
    assert out.status == STATUS_DONE
    assert [e.tool_call_id for e in out.evidence] == ["tc_good"]
    assert "acceptance_precheck" in out.status_reason or "univerifiable" in out.status_reason or "tc_bad" in out.status_reason


def test_precheck_done_all_invalid_demotes_blocked(tmp_path: Path):
    ar = AgentResult(
        subtask_id="sub_1",
        status=STATUS_DONE,
        conclusion="c",
        evidence=(Evidence(tool_call_id="tc_missing", claim="x"),),
        uncertain="",
        next="",
        stop_when_met=True,
        status_reason="ok",
    )
    out = precheck_agent_result(tmp_path, ar)
    assert out.status == STATUS_BLOCKED
    assert out.evidence == ()
    assert "acceptance_precheck" in out.status_reason


def test_precheck_never_elevates_nondone(tmp_path: Path):
    write_record(tmp_path, _rec("tc_a"))
    for status in (STATUS_BLOCKED, STATUS_NEED_DECISION):
        ar = AgentResult(
            subtask_id="sub_1",
            status=status,
            conclusion="c",
            evidence=(Evidence(tool_call_id="tc_a", claim="x"),),
            uncertain="",
            next="",
            stop_when_met=False,
            status_reason="r",
        )
        out = precheck_agent_result(tmp_path, ar)
        assert out.status == status

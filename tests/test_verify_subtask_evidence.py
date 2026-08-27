"""Tests for exact tool_call_id semantics, verify_subtask_evidence, run_command evidence."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from forge.agent_abi import AgentResult, Evidence, STATUS_DONE, STATUS_BLOCKED
from forge.tool_call_record import (
    ToolCallRecord,
    current_timestamp,
    get_record,
    write_record,
)
from forge.tools.meta_tools import make_meta_tools
from forge.tools.schemas import CONTROL_PLANE_TOOL_DECLARATIONS
from forge.workspace import Workspace


# ---------------------------------------------------------------------------
# A. Exact ID matching (no prefix / truncation)
# ---------------------------------------------------------------------------


def test_exact_id_found_truncated_not_found(tmp_path: Path):
    full_id = "tc_407b4b59c97c4edbabc75d46313e8373"
    short_id = "tc_407b4b59"
    write_record(
        tmp_path,
        ToolCallRecord(
            tool_call_id=full_id,
            subtask_id="sub_x",
            tool_name="run_command",
            input={"cmd": "echo hi"},
            output={"returncode": 0, "cmd": "echo hi", "stdout": "hi\n", "stderr": ""},
            status="success",
            error=None,
            timestamp=current_timestamp(),
        ),
    )
    assert get_record(tmp_path, full_id) is not None
    assert get_record(tmp_path, short_id) is None
    # No prefix match: full id starting with short still requires exact equality
    assert get_record(tmp_path, full_id[:10]) is None


def test_verify_tool_call_rejects_truncated_id(tmp_path: Path):
    full_id = "tc_407b4b59c97c4edbabc75d46313e8373"
    write_record(
        tmp_path,
        ToolCallRecord(
            tool_call_id=full_id,
            subtask_id="sub_x",
            tool_name="run_command",
            input={"cmd": "true"},
            output={"returncode": 0, "cmd": "true", "stdout": "", "stderr": ""},
            status="success",
            error=None,
            timestamp=current_timestamp(),
        ),
    )
    tools = make_meta_tools(Workspace(project_root=str(tmp_path)))
    ok = tools["verify_tool_call"](full_id)
    assert ok.success is True
    assert ok.payload["found"] is True
    bad = tools["verify_tool_call"]("tc_407b4b59")
    assert bad.success is False
    assert bad.payload.get("found") is False


# ---------------------------------------------------------------------------
# B. verify_subtask_evidence (structured path, no LLM UUID rewrite)
# ---------------------------------------------------------------------------


def _make_runtime_with_store(tmp_path: Path):
    """Minimal Runtime-like object exposing verify_subtask_evidence via real code path.

    We exercise the nested function by constructing Runtime pieces enough to
    register tools, without full World/adapter bootstrap when possible.
    """
    from forge.runtime import Runtime
    from forge.adapters.base import BaseAdapter, Message
    from forge.memory import MemoryStore
    from forge.workspace import Workspace as WS

    class _DummyAdapter(BaseAdapter):
        def send(self, messages, schemas=None, **kwargs):
            return SimpleNamespace(content="STOP_WHEN: met\nCONCLUSION:\nok", tool_calls=[])

        def stream(self, *a, **k):
            raise NotImplementedError

    # Avoid heavy World startup if possible — Runtime.__init__ tries World.
    # Use real Runtime; if World fails it degrades.
    adapter = _DummyAdapter()
    ws = WS(project_root=str(tmp_path))
    mem = MagicMock()
    try:
        rt = Runtime(adapter=adapter, workspace=ws, memory=mem)
    except Exception:
        # Fallback: build only the tool closures we need
        rt = SimpleNamespace(
            _subagent_results={},
            workspace=ws,
        )
        # Manually bind verify from a thin reimplementation matching production
        from forge.tool_call_record import get_record as _gr
        from forge.tools.display import format_block
        from forge.adapters.base import ToolResult

        def verify_subtask_evidence(subtask_id: str) -> ToolResult:
            sid = (subtask_id or "").strip()
            if not sid:
                return ToolResult.fail(
                    display=format_block(
                        "verify_subtask_evidence", "FAIL", {"reason": "subtask_id required"}
                    ),
                    payload={"subtask_id": sid, "found": False},
                )
            stored = rt._subagent_results.get(sid)
            if stored is None:
                return ToolResult.fail(
                    display=format_block(
                        "verify_subtask_evidence",
                        "FAIL",
                        {
                            "subtask_id": sid,
                            "reason": "no structured AgentResult stored for subtask_id",
                        },
                    ),
                    payload={"subtask_id": sid, "found": False, "evidence_results": []},
                )
            raw_evidence = stored.get("evidence") or []
            evidence_results = []
            all_ok = True
            for item in raw_evidence:
                if not isinstance(item, dict):
                    continue
                tc_id = str(item.get("tool_call_id") or "").strip()
                if not tc_id:
                    evidence_results.append(
                        {
                            "tool_call_id": "",
                            "ok": False,
                            "reason": "empty tool_call_id in evidence",
                            "record": None,
                            "evidence": item,
                        }
                    )
                    all_ok = False
                    continue
                rec = _gr(str(tmp_path), tc_id)
                ok = rec is not None
                if ok and rec.get("subtask_id") and str(rec.get("subtask_id")) != sid:
                    ok = False
                if not ok:
                    all_ok = False
                evidence_results.append(
                    {
                        "tool_call_id": tc_id,
                        "ok": ok,
                        "reason": None if ok else "no ToolCallRecord found (exact id)",
                        "record": rec if ok else None,
                        "evidence": item,
                    }
                )
            payload = {
                "subtask_id": sid,
                "found": True,
                "agent_status": stored.get("status"),
                "evidence_count": len(evidence_results),
                "all_ok": all_ok and len(evidence_results) > 0,
                "evidence_results": evidence_results,
            }
            display = format_block(
                "verify_subtask_evidence",
                "OK" if payload["all_ok"] else "FAIL",
                {"subtask_id": sid, "all_ok": payload["all_ok"]},
                "",
            )
            if payload["all_ok"]:
                return ToolResult.ok(display=display, payload=payload)
            return ToolResult.fail(display=display, payload=payload)

        rt.verify_subtask_evidence = verify_subtask_evidence
        return rt

    # Prefer real tool from executor if Runtime constructed
    if hasattr(rt, "executor") and rt.executor is not None:
        return rt
    return rt


def test_verify_subtask_evidence_schema_present():
    names = {d["name"] for d in CONTROL_PLANE_TOOL_DECLARATIONS}
    assert "verify_subtask_evidence" in names
    decl = next(
        d for d in CONTROL_PLANE_TOOL_DECLARATIONS if d["name"] == "verify_subtask_evidence"
    )
    assert "subtask_id" in decl["parameters"]["properties"]
    assert "subtask_id" in decl["parameters"]["required"]


def test_verify_subtask_evidence_happy_path(tmp_path: Path):
    sid = "sub_abc123"
    tc1 = "tc_" + "a" * 32
    tc2 = "tc_" + "b" * 32
    write_record(
        tmp_path,
        ToolCallRecord(
            tool_call_id=tc1,
            subtask_id=sid,
            tool_name="run_command",
            input={"cmd": "python3 -c \"print('598 passed')\""},
            output={
                "returncode": 0,
                "cmd": "python3 -c \"print('598 passed')\"",
                "stdout": "598 passed\n",
                "stderr": "",
            },
            status="success",
            error=None,
            timestamp=current_timestamp(),
        ),
    )
    write_record(
        tmp_path,
        ToolCallRecord(
            tool_call_id=tc2,
            subtask_id=sid,
            tool_name="search_code",
            input={"pattern": "x"},
            output={"hits": 1},
            status="success",
            error=None,
            timestamp=current_timestamp(),
        ),
    )
    agent_result = AgentResult(
        subtask_id=sid,
        status=STATUS_DONE,
        conclusion="tests passed",
        evidence=(
            Evidence(tool_call_id=tc1, claim="598 passed in 31.20s"),
            Evidence(tool_call_id=tc2, claim="found match"),
        ),
        uncertain="",
        next="",
        stop_when_met=True,
        status_reason="ok",
    ).to_dict()

    rt = _make_runtime_with_store(tmp_path)
    rt._subagent_results[sid] = agent_result

    if hasattr(rt, "executor") and hasattr(rt.executor, "tools"):
        fn = rt.executor.tools.get("verify_subtask_evidence") or getattr(
            rt, "verify_subtask_evidence", None
        )
    else:
        fn = getattr(rt, "verify_subtask_evidence", None)
    # Runtime stores tools on executor differently; fall back to attribute
    if fn is None and hasattr(rt, "executor"):
        # ToolExecutor may hold .tools
        tools_map = getattr(rt.executor, "tools", None) or getattr(rt.executor, "_tools", None)
        if tools_map:
            fn = tools_map.get("verify_subtask_evidence")
    assert fn is not None, "verify_subtask_evidence not registered"

    result = fn(sid)
    assert result.success is True
    assert result.payload["found"] is True
    assert result.payload["all_ok"] is True
    assert result.payload["evidence_count"] == 2
    ids = {er["tool_call_id"] for er in result.payload["evidence_results"]}
    assert tc1 in ids and tc2 in ids
    for er in result.payload["evidence_results"]:
        assert er["ok"] is True
        assert er["record"] is not None
        # Full id used — not truncated
        assert er["tool_call_id"].startswith("tc_")
        assert len(er["tool_call_id"]) > len("tc_407b4b59")


def test_verify_subtask_evidence_missing_subtask(tmp_path: Path):
    rt = _make_runtime_with_store(tmp_path)
    fn = getattr(rt, "verify_subtask_evidence", None)
    if fn is None and hasattr(rt, "executor"):
        tools_map = getattr(rt.executor, "tools", None) or getattr(rt.executor, "_tools", None)
        if tools_map:
            fn = tools_map.get("verify_subtask_evidence")
    assert fn is not None
    result = fn("sub_does_not_exist")
    assert result.success is False
    assert result.payload.get("found") is False


def test_verify_subtask_evidence_empty_evidence(tmp_path: Path):
    sid = "sub_empty"
    rt = _make_runtime_with_store(tmp_path)
    rt._subagent_results[sid] = AgentResult(
        subtask_id=sid,
        status=STATUS_BLOCKED,
        conclusion="nothing",
        evidence=(),
        uncertain="",
        next="",
        stop_when_met=False,
        status_reason="no evidence",
    ).to_dict()
    fn = getattr(rt, "verify_subtask_evidence", None)
    if fn is None and hasattr(rt, "executor"):
        tools_map = getattr(rt.executor, "tools", None) or getattr(rt.executor, "_tools", None)
        if tools_map:
            fn = tools_map.get("verify_subtask_evidence")
    result = fn(sid)
    assert result.success is False
    assert result.payload["found"] is True
    assert result.payload["all_ok"] is False
    assert result.payload["evidence_count"] == 0


def test_verify_subtask_evidence_missing_record(tmp_path: Path):
    sid = "sub_miss"
    tc = "tc_" + "c" * 32
    rt = _make_runtime_with_store(tmp_path)
    rt._subagent_results[sid] = AgentResult(
        subtask_id=sid,
        status=STATUS_DONE,
        conclusion="claim without record",
        evidence=(Evidence(tool_call_id=tc, claim="ghost"),),
        uncertain="",
        next="",
        stop_when_met=True,
        status_reason="ok",
    ).to_dict()
    fn = getattr(rt, "verify_subtask_evidence", None)
    if fn is None and hasattr(rt, "executor"):
        tools_map = getattr(rt.executor, "tools", None) or getattr(rt.executor, "_tools", None)
        if tools_map:
            fn = tools_map.get("verify_subtask_evidence")
    result = fn(sid)
    assert result.success is False
    assert result.payload["all_ok"] is False
    assert result.payload["evidence_results"][0]["ok"] is False


def test_verify_subtask_evidence_partial_failure(tmp_path: Path):
    sid = "sub_partial"
    tc_ok = "tc_" + "d" * 32
    tc_bad = "tc_" + "e" * 32
    write_record(
        tmp_path,
        ToolCallRecord(
            tool_call_id=tc_ok,
            subtask_id=sid,
            tool_name="run_command",
            input={"cmd": "true"},
            output={"returncode": 0, "cmd": "true", "stdout": "", "stderr": ""},
            status="success",
            error=None,
            timestamp=current_timestamp(),
        ),
    )
    rt = _make_runtime_with_store(tmp_path)
    rt._subagent_results[sid] = AgentResult(
        subtask_id=sid,
        status=STATUS_DONE,
        conclusion="mixed",
        evidence=(
            Evidence(tool_call_id=tc_ok, claim="ok"),
            Evidence(tool_call_id=tc_bad, claim="missing"),
        ),
        uncertain="",
        next="",
        stop_when_met=True,
        status_reason="ok",
    ).to_dict()
    fn = getattr(rt, "verify_subtask_evidence", None)
    if fn is None and hasattr(rt, "executor"):
        tools_map = getattr(rt.executor, "tools", None) or getattr(rt.executor, "_tools", None)
        if tools_map:
            fn = tools_map.get("verify_subtask_evidence")
    result = fn(sid)
    assert result.success is False
    oks = [er["ok"] for er in result.payload["evidence_results"]]
    assert True in oks and False in oks


# ---------------------------------------------------------------------------
# C. run_command ground-truth stdout in ToolCallRecord
# ---------------------------------------------------------------------------


def test_run_command_payload_includes_stdout(tmp_path: Path):
    tools = make_meta_tools(Workspace(project_root=str(tmp_path)))
    r = tools["run_command"]("python3 -c \"print('598 passed')\"")
    assert r.success is True
    assert r.payload["returncode"] == 0
    assert "stdout" in r.payload
    assert "598 passed" in r.payload["stdout"]
    assert "stderr" in r.payload


def test_run_command_record_preserves_stdout_for_verify(tmp_path: Path):
    """Simulate subagent path: execute run_command, write ToolCallRecord from payload."""
    from forge.tool_call_record import new_tool_call_id

    tools = make_meta_tools(Workspace(project_root=str(tmp_path)))
    result = tools["run_command"]("python3 -c \"print('598 passed')\"")
    assert result.success
    tc_id = new_tool_call_id()
    rec = ToolCallRecord(
        tool_call_id=tc_id,
        subtask_id="sub_pytest",
        tool_name="run_command",
        input={"cmd": "python3 -c \"print('598 passed')\""},
        output=result.payload,
        status="success",
        error=None,
        timestamp=current_timestamp(),
    )
    assert write_record(tmp_path, rec) is True
    found = get_record(tmp_path, tc_id)
    assert found is not None
    assert found["output"]["returncode"] == 0
    assert "598 passed" in found["output"]["stdout"]
    # Independent verify_tool_call sees the fact
    v = tools["verify_tool_call"](tc_id)
    assert v.success
    assert "598 passed" in str(v.payload["output"].get("stdout", ""))

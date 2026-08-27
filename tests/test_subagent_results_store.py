"""Persistence for structured AgentResult (_subagent_results JSONL)."""
from __future__ import annotations

import json
from pathlib import Path

from forge.agent_abi import AgentResult, Evidence, STATUS_DONE, STATUS_BLOCKED
from forge.subagent_results_store import (
    RECORD_RELATIVE_PATH,
    append_subagent_result,
    load_subagent_results,
)
from forge.tool_call_record import (
    ToolCallRecord,
    current_timestamp,
    write_record,
)


def _ar(sid: str, claim: str = "ok", status: str = STATUS_DONE) -> dict:
    return AgentResult(
        subtask_id=sid,
        status=status,
        conclusion=claim,
        evidence=(Evidence(tool_call_id="tc_" + "f" * 32, claim=claim),),
        uncertain="",
        next="",
        stop_when_met=status == STATUS_DONE,
        status_reason="test",
    ).to_dict()


def test_append_and_load_roundtrip(tmp_path: Path):
    d = _ar("sub_1", "first")
    assert append_subagent_result(tmp_path, d) is True
    loaded = load_subagent_results(tmp_path)
    assert "sub_1" in loaded
    assert loaded["sub_1"]["conclusion"] == "first"
    assert loaded["sub_1"]["subtask_id"] == "sub_1"


def test_last_record_wins_for_same_subtask_id(tmp_path: Path):
    assert append_subagent_result(tmp_path, _ar("sub_x", "v1")) is True
    assert append_subagent_result(tmp_path, _ar("sub_x", "v2")) is True
    loaded = load_subagent_results(tmp_path)
    assert loaded["sub_x"]["conclusion"] == "v2"


def test_corrupt_line_skipped_others_loaded(tmp_path: Path):
    path = tmp_path / RECORD_RELATIVE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    good1 = _ar("sub_a", "A")
    good2 = _ar("sub_b", "B")
    with open(path, "w", encoding="utf-8") as f:
        f.write(json.dumps(good1) + "\n")
        f.write("{not valid json\n")
        f.write(json.dumps(good2) + "\n")
        f.write("null\n")
        f.write('{"no_subtask_id": true}\n')
    loaded = load_subagent_results(tmp_path)
    assert set(loaded.keys()) == {"sub_a", "sub_b"}
    assert loaded["sub_a"]["conclusion"] == "A"
    assert loaded["sub_b"]["conclusion"] == "B"


def test_missing_file_returns_empty(tmp_path: Path):
    assert load_subagent_results(tmp_path) == {}


def test_verify_after_reload_uses_persisted_result(tmp_path: Path):
    """Simulate restart: append, clear memory, reload, verify_subtask_evidence."""
    from forge.tools.display import format_block
    from forge.adapters.base import ToolResult
    from forge.tool_call_record import get_record

    sid = "sub_persist"
    tc_id = "tc_" + "a" * 32
    write_record(
        tmp_path,
        ToolCallRecord(
            tool_call_id=tc_id,
            subtask_id=sid,
            tool_name="run_command",
            input={"cmd": "python3 -c \"print('598 passed')\""},
            output={
                "returncode": 0,
                "cmd": "x",
                "stdout": "598 passed\n",
                "stderr": "",
            },
            status="success",
            error=None,
            timestamp=current_timestamp(),
        ),
    )
    ar = AgentResult(
        subtask_id=sid,
        status=STATUS_DONE,
        conclusion="tests ok",
        evidence=(Evidence(tool_call_id=tc_id, claim="598 passed"),),
        uncertain="",
        next="",
        stop_when_met=True,
        status_reason="ok",
    ).to_dict()
    assert append_subagent_result(tmp_path, ar) is True

    # "Restart": only load from disk
    store = load_subagent_results(tmp_path)
    assert sid in store

    def verify_subtask_evidence(subtask_id: str) -> ToolResult:
        s = (subtask_id or "").strip()
        stored = store.get(s)
        if stored is None:
            return ToolResult.fail(
                display="missing",
                payload={"subtask_id": s, "found": False, "evidence_results": []},
            )
        results = []
        all_ok = True
        for item in stored.get("evidence") or []:
            tc = str(item.get("tool_call_id") or "").strip()
            rec = get_record(tmp_path, tc)
            ok = rec is not None
            if not ok:
                all_ok = False
            results.append({"tool_call_id": tc, "ok": ok, "record": rec})
        payload = {
            "subtask_id": s,
            "found": True,
            "all_ok": all_ok and len(results) > 0,
            "evidence_results": results,
        }
        if payload["all_ok"]:
            return ToolResult.ok(display="ok", payload=payload)
        return ToolResult.fail(display="fail", payload=payload)

    r = verify_subtask_evidence(sid)
    assert r.success is True
    assert r.payload["all_ok"] is True
    assert "598 passed" in r.payload["evidence_results"][0]["record"]["output"]["stdout"]


def test_verify_does_not_append_duplicate(tmp_path: Path):
    path = tmp_path / RECORD_RELATIVE_PATH
    ar = _ar("sub_once", "once")
    assert append_subagent_result(tmp_path, ar) is True
    before = path.read_text(encoding="utf-8").count("\n")
    # simulate verify: only reads store, never appends
    loaded = load_subagent_results(tmp_path)
    assert "sub_once" in loaded
    after = path.read_text(encoding="utf-8").count("\n")
    assert before == after == 1


def test_runtime_loads_persisted_on_init(tmp_path: Path):
    """Runtime.__init__ loads JSONL into _subagent_results."""
    from types import SimpleNamespace
    from unittest.mock import MagicMock, patch

    sid = "sub_rt"
    assert append_subagent_result(tmp_path, _ar(sid, "from_disk")) is True

    from forge.adapters.base import BaseAdapter
    from forge.memory import MemoryStore
    from forge.workspace import Workspace

    class _Dummy(BaseAdapter):
        def send(self, messages, schemas=None, **kwargs):
            return SimpleNamespace(content="", tool_calls=[])

        def send_stream(self, *a, **k):
            raise NotImplementedError

    with patch("forge.runtime.WorldRuntime") as WR:
        WR.return_value.ensure_identity = MagicMock()
        from forge.runtime import Runtime

        rt = Runtime(
            adapter=_Dummy(),
            workspace=Workspace(project_root=str(tmp_path)),
            memory=MemoryStore(),
        )
    assert sid in rt._subagent_results
    assert rt._subagent_results[sid]["conclusion"] == "from_disk"

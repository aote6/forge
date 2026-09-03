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


def test_main_audited_tool_names_membership():
    from forge.tools.schemas import (
        MAIN_AUDITED_TOOL_NAMES,
        MAIN_READ_ONLY_TOOL_NAMES,
        MAIN_READ_ONLY_TOOL_DECLARATIONS,
    )

    # Original MAIN_READ_ONLY members must remain audited
    for name in (
        "read_file",
        "read_function",
        "glob_files",
        "search_code",
        "find_symbol_definition",
        "get_repo_map",
        "git_diff",
    ):
        assert name in MAIN_READ_ONLY_TOOL_NAMES
        assert name in MAIN_AUDITED_TOOL_NAMES

    assert "resolve_sync_decision" in MAIN_AUDITED_TOOL_NAMES
    assert "spawn_subagent" in MAIN_AUDITED_TOOL_NAMES
    assert "get_runtime_state" not in MAIN_AUDITED_TOOL_NAMES

    # Control-plane tools must not leak into READ_ONLY exposure
    assert "resolve_sync_decision" not in MAIN_READ_ONLY_TOOL_NAMES
    assert "spawn_subagent" not in MAIN_READ_ONLY_TOOL_NAMES
    decl_names = {d["name"] for d in MAIN_READ_ONLY_TOOL_DECLARATIONS}
    assert "resolve_sync_decision" not in decl_names
    assert "spawn_subagent" not in decl_names


def test_main_loop_audits_resolve_sync_decision(tmp_path):
    """Integration: resolve_sync_decision passes main-loop MAIN_AUDITED path.

    Runtime is constructed via the real constructor (same pattern as
    tests/test_user_stop.py). Adapter/executor are mocked; the test still
    drives _run_conversation → per-tool-call loop → MAIN_AUDITED_TOOL_NAMES
    → _record_main_tool_call → .forge/tool_call_records.jsonl.
    """
    import json

    from forge.adapters.base import Message, ToolCall, ToolResult
    from forge.memory import MemoryStore
    from forge.runtime import Runtime
    from forge.workspace import Workspace

    class _FakeAdapter:
        model_name = "t"

        def __init__(self, responses):
            self._responses = list(responses)

        def send(self, messages, schemas):
            return self._responses.pop(0) if self._responses else Message(
                role="assistant", content=""
            )

    class _Ex:
        """Stub executor: avoid real sync/subagent side effects."""

        tools: dict = {}

        def execute(self, tc):
            return ToolResult.ok(
                display="resolve_sync_decision: abort",
                payload={"direction": (tc.arguments or {}).get("direction")},
            )

    adapter = _FakeAdapter(
        [
            Message(
                role="assistant",
                content=None,
                tool_calls=[
                    ToolCall(
                        id="tc_rsd_1",
                        name="resolve_sync_decision",
                        arguments={"direction": "abort"},
                    )
                ],
            ),
            Message(role="assistant", content="done", tool_calls=None),
        ]
    )
    ws = Workspace(project_root=str(tmp_path))
    rt = Runtime(adapter, ws, MemoryStore())
    # Replace only the executor after real construction (expensive tools mocked).
    rt.executor = _Ex()

    rt._run_conversation("resolve abort")

    records_path = tmp_path / ".forge" / "tool_call_records.jsonl"
    assert records_path.is_file(), "expected tool_call_records.jsonl from main audit path"
    lines = [
        ln for ln in records_path.read_text(encoding="utf-8").splitlines() if ln.strip()
    ]
    assert lines, "expected at least one ToolCallRecord"

    found = None
    for ln in lines:
        rec = json.loads(ln)
        if rec.get("tool_name") == "resolve_sync_decision":
            found = rec
            break
    assert found is not None
    assert found["actor"] == "main"
    assert found["tool_name"] == "resolve_sync_decision"
    assert found["input"]["direction"] == "abort"


def test_main_loop_audits_spawn_subagent(tmp_path):
    """Integration: spawn_subagent goes through same main-loop audit path.

    Real Runtime(...) construction; executor stubbed so no real sub-Runtime runs.
    """
    import json

    from forge.adapters.base import Message, ToolCall, ToolResult
    from forge.memory import MemoryStore
    from forge.runtime import Runtime
    from forge.workspace import Workspace

    class _FakeAdapter:
        model_name = "t"

        def __init__(self, responses):
            self._responses = list(responses)

        def send(self, messages, schemas):
            return self._responses.pop(0) if self._responses else Message(
                role="assistant", content=""
            )

    class _Ex:
        tools: dict = {}

        def execute(self, tc):
            # Do not spawn a real sub-Runtime; only exercise audit path.
            return ToolResult.ok(
                display="spawn_subagent mocked",
                payload={"goal": (tc.arguments or {}).get("goal")},
            )

    adapter = _FakeAdapter(
        [
            Message(
                role="assistant",
                content=None,
                tool_calls=[
                    ToolCall(
                        id="tc_spawn_1",
                        name="spawn_subagent",
                        arguments={
                            "goal": "noop",
                            "done_when": "done",
                            "stop_when": "stop",
                        },
                    )
                ],
            ),
            Message(role="assistant", content="done", tool_calls=None),
        ]
    )
    ws = Workspace(project_root=str(tmp_path))
    rt = Runtime(adapter, ws, MemoryStore())
    rt.executor = _Ex()

    rt._run_conversation("spawn")

    records_path = tmp_path / ".forge" / "tool_call_records.jsonl"
    assert records_path.is_file()
    lines = [
        ln for ln in records_path.read_text(encoding="utf-8").splitlines() if ln.strip()
    ]
    found = None
    for ln in lines:
        rec = json.loads(ln)
        if rec.get("tool_name") == "spawn_subagent":
            found = rec
            break
    assert found is not None
    assert found["actor"] == "main"
    assert found["tool_name"] == "spawn_subagent"

"""ToolCallRecord — append-only immutable log of Runtime tool calls.

Each real tool invocation (main AI or subagent) gets a tool_call_id allocated
before execution, and a record written after execution completes. Records
are never modified or deleted once written; only appended.

Fields include actor ("main" | "subagent") so the same log covers both
control-plane reads and execution-plane tools. subtask_id is required for
subagent calls and empty string for main-agent calls.

Storage: <project_root>/.forge/tool_call_records.jsonl (JSON Lines, one
record per line). This is a separate stream from session_changes.jsonl —
it exists to make tool facts independently verifiable (Evidence for
subtasks; durable proof of main reads), and must not be conflated with
world-state change logs.

Legacy lines without "actor" are treated as actor="subagent" on read.
"""
from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


RECORD_RELATIVE_PATH = os.path.join(".forge", "tool_call_records.jsonl")


def new_tool_call_id() -> str:
    """Allocate a fresh tool_call_id.

    Must be called *before* the tool executes, so the id exists
    regardless of whether execution succeeds, fails, or raises.
    """
    return f"tc_{uuid.uuid4().hex}"


@dataclass(frozen=True)
class ToolCallRecord:
    """Immutable record of one real tool invocation.

    Once written, a record is never edited or removed — only appended to
    the log. Downstream consumers (constraint layer, AgentResult assembly,
    main-agent acceptance) treat every field here as ground truth, distinct
    from anything a model asserts about the call.
    """

    tool_call_id: str
    subtask_id: str  # subagent: real id; main: ""
    tool_name: str
    input: dict[str, Any]
    output: Any
    status: str  # "success" | "error"
    error: str | None
    timestamp: float  # unix epoch seconds, UTC
    actor: str = "subagent"  # "main" | "subagent"; new writers pass explicitly

    def to_json_line(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False, default=str)


def _records_path(project_root: str | os.PathLike) -> Path:
    return Path(project_root) / RECORD_RELATIVE_PATH


def write_record(project_root: str | os.PathLike, record: ToolCallRecord) -> bool:
    """Append one record to the JSONL log.

    Returns True on success, False on any failure. Never raises — a
    logging failure must never be allowed to affect the ToolResult of the
    tool call it is trying to record. Callers must not let a False return
    value change or block the already-produced tool result.
    """
    try:
        path = _records_path(project_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        line = record.to_json_line()
        # Append-only: 'a' mode only. No seek, no rewrite of prior lines,
        # no truncation.
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        return True
    except Exception:
        # Deliberately swallow everything. Logging is best-effort and must
        # never propagate into the tool-execution path.
        return False


def get_record(project_root: str | os.PathLike, tool_call_id: str) -> dict[str, Any] | None:
    """Look up one record by tool_call_id.

    Returns a plain dict (not the frozen dataclass) since this is a
    read-side lookup for the constraint layer / main-agent acceptance flow,
    not for further construction of records. Returns None if the file is
    missing or no record matches; never raises.

    Records are never rewritten in place, so there should be at most one
    entry per tool_call_id — the scan is still exhaustive and defensive
    rather than assuming that invariant holds.
    """
    path = _records_path(project_root)
    if not path.exists():
        return None

    found: dict[str, Any] | None = None
    try:
        with open(path, "r", encoding="utf-8") as f:
            for raw_line in f:
                raw_line = raw_line.strip()
                if not raw_line:
                    continue
                try:
                    obj = json.loads(raw_line)
                except json.JSONDecodeError:
                    # A corrupted line must not break lookup of other
                    # records; skip and keep scanning.
                    continue
                if obj.get("tool_call_id") == tool_call_id:
                    if not obj.get("actor"):
                        obj["actor"] = "subagent"
                    found = obj
        return found
    except Exception:
        return None


def list_records_for_subtask(
    project_root: str | os.PathLike, subtask_id: str
) -> list[dict[str, Any]]:
    """Return all ToolCallRecord dicts for a subtask_id (append order).

    Missing file / corrupt lines → skipped. Never raises.
    Used by Durable Pause fact summary (design §7.4): read the FULL set
    for the subtask, not only up to checkpoint.last_tool_call_id.
    """
    path = _records_path(project_root)
    out: list[dict[str, Any]] = []
    if not path.exists():
        return out
    sid = str(subtask_id or "").strip()
    if not sid:
        return out
    try:
        with open(path, "r", encoding="utf-8") as f:
            for raw_line in f:
                raw_line = raw_line.strip()
                if not raw_line:
                    continue
                try:
                    obj = json.loads(raw_line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(obj, dict):
                    continue
                if str(obj.get("subtask_id") or "") == sid:
                    if not obj.get("actor"):
                        obj["actor"] = "subagent"
                    out.append(obj)
    except Exception:
        return out
    return out


def current_timestamp() -> float:
    """UTC unix timestamp, factored out so callers don't import time directly."""
    return time.time()

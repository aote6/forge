"""Append-only store for structured AgentResult by subtask_id.

Storage: <project_root>/.forge/subagent_results.jsonl

Each line is one AgentResult.to_dict() JSON object. Load keeps the last
valid record per subtask_id. Corrupt lines are skipped; missing file is OK.
Does not store main-agent acceptance judgments or narrative.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


RECORD_RELATIVE_PATH = os.path.join(".forge", "subagent_results.jsonl")


def _path(project_root: str | os.PathLike) -> Path:
    return Path(project_root) / RECORD_RELATIVE_PATH


def load_subagent_results(project_root: str | os.PathLike) -> dict[str, dict[str, Any]]:
    """Load last valid AgentResult dict per subtask_id.

    Missing file → empty dict. Corrupt lines skipped. Never raises.
    """
    path = _path(project_root)
    out: dict[str, dict[str, Any]] = {}
    if not path.exists():
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
                sid = str(obj.get("subtask_id") or "").strip()
                if not sid:
                    continue
                out[sid] = obj
    except Exception:
        return out
    return out


def append_subagent_result(
    project_root: str | os.PathLike,
    agent_result_dict: dict[str, Any],
) -> bool:
    """Append one AgentResult.to_dict() line. Best-effort; never raises.

    Returns True on success, False on failure. Callers must not block
    spawn_subagent on a False return.
    """
    if not isinstance(agent_result_dict, dict):
        return False
    sid = str(agent_result_dict.get("subtask_id") or "").strip()
    if not sid:
        return False
    try:
        path = _path(project_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(agent_result_dict, ensure_ascii=False, default=str)
        with open(path, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        return True
    except Exception:
        return False

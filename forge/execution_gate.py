"""Execution Gate — confirmation classification for subagent tool calls.

Phase 2 (MAIN_SUBAGENT_IMPLEMENTATION_DESIGN v2):
  Authorization boundary (not_allowed / scope) stays in constraint_enforcer.
  This module only answers: ALLOW vs PAUSE inside the authorized region.

Layer A: run_command cmd static prefix classification.
Does not evaluate model semantics. Compound commands → unknown → PAUSE.
"""
from __future__ import annotations

from typing import Any

from forge.command_class_prefixes import (
    COMMAND_CLASS_UNKNOWN,
    resolve_command_class,
)
from forge.tools.schemas import MUTATION_TOOL_NAMES, RECONCILIATION_TOOL_NAMES

# Gate decisions (confirmation layer only)
ALLOW = "ALLOW"
PAUSE = "PAUSE"

# Prefix classes treated as read-only at the confirmation layer
_READ_ONLY_CLASSES = frozenset({"test", "vcs_read", "read_only", "type_check"})

# Prefix classes that require user confirmation
_PAUSE_CLASSES = frozenset({"vcs_write", "destructive", "destructive_write", "unknown"})

# Explicit mutation tool names → always PAUSE (undo_last_tx is recovery → ALLOW)
_RECOVERY_TOOLS = frozenset({"undo_last_tx"})

# Tools that never mutate the engineering world at confirmation layer
_ALWAYS_ALLOW_TOOLS = frozenset({
    "read_file",
    "read_function",
    "glob_files",
    "search_code",
    "find_symbol_definition",
    "get_repo_map",
    "git_diff",
    "run_test_structured",
    "run_type_check",
    "world_info",
    "list_world_objects",
    "get_world_object",
    "list_world_links",
    "search_history",
    "resolve_path_object",
    "session_changes",
    "project_memory",
    "project_review",
    "web_fetch",
})



def _normalize_cmd(cmd: str) -> str:
    return " ".join((cmd or "").strip().split())


# resolve_command_class imported from forge.command_class_prefixes
def resolve_run_command_gate(cmd: str) -> str:
    """Layer A: ALLOW for read-only classes; PAUSE otherwise."""
    cls = resolve_command_class(cmd)
    if cls in _READ_ONLY_CLASSES:
        return ALLOW
    return PAUSE


def classify_for_confirmation(tool_name: str, args: dict[str, Any] | None) -> str:
    """Return ALLOW or PAUSE for one tool call (post-enforce)."""
    name = (tool_name or "").strip()
    args = args if isinstance(args, dict) else {}

    if name == "run_command":
        return resolve_run_command_gate(str(args.get("cmd") or ""))

    if name in _RECOVERY_TOOLS:
        return ALLOW

    if name in _ALWAYS_ALLOW_TOOLS:
        return ALLOW

    if name in MUTATION_TOOL_NAMES:
        return PAUSE

    if name in RECONCILIATION_TOOL_NAMES:
        # forge_sync: confirm before advancing (align with prior WRITE path)
        return PAUSE

    # Unlisted execution-plane tools: conservative PAUSE
    return PAUSE


def pause_summary(tool_name: str, args: dict[str, Any] | None) -> str:
    """Human-readable summary of the frozen write action."""
    args = args if isinstance(args, dict) else {}
    if tool_name == "str_replace":
        path = args.get("path", "?")
        old = str(args.get("old_string") or "")
        new = str(args.get("new_string") or "")
        return (
            f"str_replace path={path}\n"
            f"  old_string ({len(old)} chars): {old[:120]!r}"
            f"{'…' if len(old) > 120 else ''}\n"
            f"  new_string ({len(new)} chars): {new[:120]!r}"
            f"{'…' if len(new) > 120 else ''}"
        )
    if tool_name == "write_file":
        path = args.get("path", "?")
        content = str(args.get("content") or "")
        return f"write_file path={path} content_len={len(content)}"
    if tool_name == "run_command":
        return f"run_command cmd={args.get('cmd')!r}"
    if tool_name == "post_toot":
        text = str(args.get("text") or "")
        return f"post_toot text={text[:200]!r}{'…' if len(text) > 200 else ''}"
    try:
        import json

        blob = json.dumps(args, ensure_ascii=False)
    except Exception:
        blob = str(args)
    if len(blob) > 400:
        blob = blob[:400] + "…"
    return f"{tool_name} {blob}"

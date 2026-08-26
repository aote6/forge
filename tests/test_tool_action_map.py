"""Tests for forge.tool_action_map — static tool registry."""
from __future__ import annotations

from forge.tool_action_map import TOOL_ACTION_MAP, TOOL_ACTION_MAP_BY_NAME

# Explicit full registry (order matches tool_action_map.py as of Agent ABI v1).
EXPECTED_TOOL_NAMES = [
    "read_file",
    "read_function",
    "glob_files",
    "search_code",
    "find_symbol_definition",
    "get_repo_map",
    "git_diff",
    "run_command",
    "run_test_structured",
    "run_type_check",
    "world_info",
    "list_world_objects",
    "get_world_object",
    "list_world_links",
    "search_history",
    "resolve_path_object",
    "todo_write",
    "todo_list",
    "spawn_subagent",
    "session_changes",
    "verify_tool_call",
    "project_memory",
    "project_review",
    "web_fetch",
    "post_toot",
    "delete_toot",
    "undo_last_tx",
    "str_replace",
    "write_file",
    "create_file",
    "modify_file",
    "edit_files_batch",
    "apply_patch",
    "delete_file",
    "create_object",
    "link_objects",
    "unlink_objects",
    "forge_sync",
    "submit_plan",
]

LEGAL_ACTIONS = frozenset({
    "read",
    "write",
    "delete",
    "execute",
    "world_read",
    "world_write",
    "network",
    "control",
    "reconciliation",
})


def test_all_expected_tools_registered():
    names = {row["tool_name"] for row in TOOL_ACTION_MAP}
    for name in EXPECTED_TOOL_NAMES:
        assert name in names, f"missing tool in TOOL_ACTION_MAP: {name}"
    assert len(TOOL_ACTION_MAP) >= 38
    assert len(TOOL_ACTION_MAP) == len(EXPECTED_TOOL_NAMES)


def test_tool_names_unique():
    names = [row["tool_name"] for row in TOOL_ACTION_MAP]
    assert len(names) == len(set(names))
    assert set(TOOL_ACTION_MAP_BY_NAME) == set(names)


def test_actions_in_legal_enum():
    for row in TOOL_ACTION_MAP:
        assert row["action"] in LEGAL_ACTIONS, (row["tool_name"], row["action"])


def test_apply_patch_unparseable_path():
    row = TOOL_ACTION_MAP_BY_NAME["apply_patch"]
    assert row["path_field"] == "__UNPARSEABLE_PATH__"


def test_verify_tool_call_is_read():
    assert TOOL_ACTION_MAP_BY_NAME["verify_tool_call"]["action"] == "read"

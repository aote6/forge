"""tool_action_map - static deterministic tool_name to action mapping.

Pure data. No functions, no classes, no judgment logic.

Unregistered tools are denied by default by the constraint enforcer.
"""
from __future__ import annotations

TOOL_ACTION_MAP: list[dict[str, str]] = [
    {"tool_name": "read_file", "action": "read", "path_field": "input.path", "command_class_rule": ""},
    {"tool_name": "read_function", "action": "read", "path_field": "input.path", "command_class_rule": ""},
    {"tool_name": "glob_files", "action": "read", "path_field": "", "command_class_rule": ""},
    {"tool_name": "search_code", "action": "read", "path_field": "input.path", "command_class_rule": ""},
    {"tool_name": "find_symbol_definition", "action": "read", "path_field": "", "command_class_rule": ""},
    {"tool_name": "get_repo_map", "action": "read", "path_field": "input.root_dir", "command_class_rule": ""},
    {"tool_name": "git_diff", "action": "read", "path_field": "", "command_class_rule": ""},
    {"tool_name": "run_command", "action": "execute", "path_field": "", "command_class_rule": "prefix_match:input.cmd"},
    {"tool_name": "run_test_structured", "action": "execute", "path_field": "input.target", "command_class_rule": "fixed:test"},
    {"tool_name": "run_type_check", "action": "execute", "path_field": "input.path", "command_class_rule": "fixed:type_check"},
    {"tool_name": "world_info", "action": "world_read", "path_field": "", "command_class_rule": ""},
    {"tool_name": "list_world_objects", "action": "world_read", "path_field": "", "command_class_rule": ""},
    {"tool_name": "get_world_object", "action": "world_read", "path_field": "", "command_class_rule": ""},
    {"tool_name": "list_world_links", "action": "world_read", "path_field": "", "command_class_rule": ""},
    {"tool_name": "search_history", "action": "read", "path_field": "", "command_class_rule": ""},
    {"tool_name": "resolve_path_object", "action": "world_read", "path_field": "input.path", "command_class_rule": ""},
    {"tool_name": "todo_write", "action": "control", "path_field": "", "command_class_rule": ""},
    {"tool_name": "todo_list", "action": "control", "path_field": "", "command_class_rule": ""},
    {"tool_name": "spawn_subagent", "action": "control", "path_field": "", "command_class_rule": ""},
    {"tool_name": "session_changes", "action": "world_read", "path_field": "", "command_class_rule": ""},
    {"tool_name": "verify_tool_call", "action": "read", "path_field": "", "command_class_rule": ""},
    {"tool_name": "verify_subtask_evidence", "action": "read", "path_field": "", "command_class_rule": ""},
    {"tool_name": "project_memory", "action": "read", "path_field": "", "command_class_rule": ""},
    {"tool_name": "project_review", "action": "read", "path_field": "", "command_class_rule": ""},
    {"tool_name": "web_fetch", "action": "network", "path_field": "input.url", "command_class_rule": ""},
    {"tool_name": "post_toot", "action": "network", "path_field": "", "command_class_rule": ""},
    {"tool_name": "delete_toot", "action": "network", "path_field": "", "command_class_rule": ""},
    {"tool_name": "undo_last_tx", "action": "world_write", "path_field": "", "command_class_rule": ""},
    {"tool_name": "str_replace", "action": "write", "path_field": "input.path", "command_class_rule": ""},
    {"tool_name": "write_file", "action": "write", "path_field": "input.path", "command_class_rule": ""},
    {"tool_name": "create_file", "action": "write", "path_field": "input.path", "command_class_rule": ""},
    {"tool_name": "modify_file", "action": "world_write", "path_field": "input.path", "command_class_rule": ""},
    {"tool_name": "edit_files_batch", "action": "world_write", "path_field": "input.edits[].path", "command_class_rule": ""},
    {"tool_name": "apply_patch", "action": "write", "path_field": "__UNPARSEABLE_PATH__", "command_class_rule": ""},
    {"tool_name": "delete_file", "action": "delete", "path_field": "input.path", "command_class_rule": ""},
    {"tool_name": "create_object", "action": "world_write", "path_field": "", "command_class_rule": ""},
    {"tool_name": "link_objects", "action": "world_write", "path_field": "", "command_class_rule": ""},
    {"tool_name": "unlink_objects", "action": "world_write", "path_field": "", "command_class_rule": ""},
    {"tool_name": "forge_sync", "action": "reconciliation", "path_field": "", "command_class_rule": ""},
    {"tool_name": "resolve_sync_decision", "action": "control", "path_field": "", "command_class_rule": ""},
    {"tool_name": "resume_subtask", "action": "control", "path_field": "", "command_class_rule": ""},
    {"tool_name": "abort_subtask", "action": "control", "path_field": "", "command_class_rule": ""},
    {"tool_name": "submit_plan", "action": "control", "path_field": "", "command_class_rule": ""},
]

TOOL_ACTION_MAP_BY_NAME: dict[str, dict[str, str]] = {
    row["tool_name"]: row for row in TOOL_ACTION_MAP
}

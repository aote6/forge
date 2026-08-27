"""Phase 3: main AI prompt must encode control-plane behavior contract."""
from __future__ import annotations

from forge.system_prompt import SYSTEM_INSTRUCTION


def test_prompt_has_no_execution_tool_instructions():
    for tool in (
        "read_file",
        "write_file",
        "str_replace",
        "run_command",
        "glob_files",
        "search_code",
        "post_toot",
        "forge_sync",
        "project_review",
        "session_changes",
        "project_memory",
        "undo_last_tx",
        "apply_patch",
    ):
        assert tool not in SYSTEM_INSTRUCTION, tool


def test_prompt_declares_spawn_subagent_default_for_engineering():
    assert "spawn_subagent" in SYSTEM_INSTRUCTION
    assert "默认派发" in SYSTEM_INSTRUCTION
    assert "派发给子 AI" in SYSTEM_INSTRUCTION or "派发给子AI" in SYSTEM_INSTRUCTION


def test_prompt_defines_direct_handling_exceptions():
    assert "直接处理" in SYSTEM_INSTRUCTION or "直接回答" in SYSTEM_INSTRUCTION
    assert "纯对话" in SYSTEM_INSTRUCTION
    assert "只分析" in SYSTEM_INSTRUCTION


def test_prompt_requires_clarification_before_unsafe_task():
    assert "澄清" in SYSTEM_INSTRUCTION
    assert "goal" in SYSTEM_INSTRUCTION
    assert "done_when" in SYSTEM_INSTRUCTION
    assert "stop_when" in SYSTEM_INSTRUCTION


def test_prompt_requires_tool_call_record_verification():
    assert "verify_tool_call" in SYSTEM_INSTRUCTION
    assert "tool_call_id" in SYSTEM_INSTRUCTION
    assert "ToolCallRecord" in SYSTEM_INSTRUCTION


def test_prompt_rejects_blind_conclusion_trust():
    assert "conclusion" in SYSTEM_INSTRUCTION.lower()
    assert "不得只信" in SYSTEM_INSTRUCTION or "不得" in SYSTEM_INSTRUCTION

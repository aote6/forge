"""Phase 3: main AI prompt must encode control-plane behavior contract."""
from __future__ import annotations

from forge.system_prompt import SYSTEM_INSTRUCTION


def test_prompt_has_no_execution_tool_instructions():
    # These tools must not appear as tools the main AI can call directly.
    for tool in (
        "read_file",
        "write_file",
        "str_replace",
        "run_command",
        "glob_files",
        "search_code",
        "post_toot",
        "project_review",
        "session_changes",
        "project_memory",
        "undo_last_tx",
        "apply_patch",
    ):
        assert tool not in SYSTEM_INSTRUCTION, tool
    # forge_sync appears only inside AgentTask instructions, never as a
    # direct main-AI tool.
    assert "forge_sync 返回 IN_SYNC" in SYSTEM_INSTRUCTION


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


def test_prompt_has_sync_handling_rules():
    assert "同步场景下的行为" in SYSTEM_INSTRUCTION
    assert "FAST_FORWARD" in SYSTEM_INSTRUCTION
    assert "CONFLICT" in SYSTEM_INSTRUCTION
    assert "IN_SYNC" in SYSTEM_INSTRUCTION
    assert "先向用户说明" in SYSTEM_INSTRUCTION


def test_prompt_requires_direction_in_sync_agent_task():
    assert "Disk → World" in SYSTEM_INSTRUCTION
    assert "World → Disk" in SYSTEM_INSTRUCTION
    assert "goal" in SYSTEM_INSTRUCTION


def test_prompt_requires_following_user_language():
    assert "语言跟随" in SYSTEM_INSTRUCTION
    assert "与用户输入相同的语言" in SYSTEM_INSTRUCTION
    assert "不要因为系统提示词是中文就默认输出中文" in SYSTEM_INSTRUCTION

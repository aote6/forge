"""Layer B VERIFY side-effect authorization tests."""
from __future__ import annotations

from pathlib import Path

from forge.execution_gate import (
    ALLOW,
    VERIFY_SIDE_EFFECT_PATTERNS,
    VERIFY_TOOL_NAMES,
    classify_for_confirmation,
)
from forge.subagent import _layer_b_unauthorized_diff


def test_verify_tools_classify_allow():
    for name in VERIFY_TOOL_NAMES:
        assert classify_for_confirmation(name, {}) == ALLOW, name


def test_verify_tools_not_in_always_allow():
    from forge.execution_gate import _ALWAYS_ALLOW_TOOLS

    assert VERIFY_TOOL_NAMES.isdisjoint(_ALWAYS_ALLOW_TOOLS)


def test_run_test_structured_side_effects_authorized():
    patterns = VERIFY_SIDE_EFFECT_PATTERNS["run_test_structured"]
    before = {
        ".pytest_cache/x": "old",
        "__pycache__/y": "old",
        ".forge/last_test_result.json": "old",
    }
    after = {
        ".pytest_cache/x": "new",
        "__pycache__/y": "new",
        ".forge/last_test_result.json": "new",
    }
    remaining = _layer_b_unauthorized_diff("/tmp/fake_root", before, after, patterns)
    assert remaining == []


def test_verify_tool_engineering_change_caught():
    patterns = VERIFY_SIDE_EFFECT_PATTERNS["run_test_structured"]
    before = {"forge/runtime.py": "old", ".pytest_cache/x": "old"}
    after = {"forge/runtime.py": "new", ".pytest_cache/x": "new"}
    remaining = _layer_b_unauthorized_diff("/tmp/fake_root", before, after, patterns)
    assert "forge/runtime.py" in remaining
    assert ".pytest_cache/x" not in remaining


def test_verify_side_effect_patterns_are_project_relative():
    for name, patterns in VERIFY_SIDE_EFFECT_PATTERNS.items():
        for pat in patterns:
            assert not pat.startswith("/"), (name, pat)
            assert not pat.startswith(".."), (name, pat)

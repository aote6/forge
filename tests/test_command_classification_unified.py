"""Phase 2.1: single-source command classification + compound bypass closure."""
from __future__ import annotations

from forge.command_class_prefixes import (
    COMMAND_CLASS_UNKNOWN,
    is_compound_shell_command,
    resolve_command_class,
)
from forge.constraint_enforcer import resolve_command_class as enforcer_resolve
from forge.execution_gate import ALLOW, PAUSE, resolve_run_command_gate


BYPASS_CASES = [
    "git diff\nrm file",
    "git diff & rm file",
    "git diff > file",
    "git diff >> file",
    "git diff < file",
    "git diff && git commit",
    "git diff | something",
    "git diff $(rm file)",
    "git diff `rm file`",
]

READONLY_ALLOW = [
    "git status",
    "git diff",
    "git log",
    "pytest",
    "pytest tests/",
    "python -m pytest tests/",
]


def test_is_compound_catches_bypass_vectors():
    for cmd in BYPASS_CASES:
        assert is_compound_shell_command(cmd) is True, cmd


def test_resolve_command_class_unknown_for_compounds():
    for cmd in BYPASS_CASES:
        assert resolve_command_class(cmd) == COMMAND_CLASS_UNKNOWN, cmd


def test_enforcer_uses_shared_resolve():
    for cmd in BYPASS_CASES:
        assert enforcer_resolve(cmd) == COMMAND_CLASS_UNKNOWN, cmd


def test_gate_pause_for_compounds():
    for cmd in BYPASS_CASES:
        assert resolve_run_command_gate(cmd) == PAUSE, cmd


def test_readonly_still_allow():
    for cmd in READONLY_ALLOW:
        assert is_compound_shell_command(cmd) is False, cmd
        assert resolve_run_command_gate(cmd) == ALLOW, cmd


def test_enforcer_and_gate_agree_on_class():
    """No split-brain: both layers use the same resolve_command_class."""
    samples = BYPASS_CASES + READONLY_ALLOW + ["rm -rf x", "git commit -m x", "unknown_bin"]
    for cmd in samples:
        assert enforcer_resolve(cmd) == resolve_command_class(cmd), cmd


def test_only_one_resolve_command_class_definition():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "forge"
    defs = []
    for path in root.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for i, line in enumerate(text.splitlines(), 1):
            if line.startswith("def resolve_command_class("):
                defs.append(f"{path.relative_to(root.parent)}:{i}")
    assert len(defs) == 1, defs
    assert defs[0].startswith("forge/command_class_prefixes.py:"), defs

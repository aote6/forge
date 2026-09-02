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


def test_execution_gate_has_no_local_compound_regex():
    from pathlib import Path

    p = Path(__file__).resolve().parents[1] / "forge" / "execution_gate.py"
    text = p.read_text(encoding="utf-8")
    assert "_COMPOUND_RE" not in text
    assert "import re" not in text


# --- P1: common read-only shell investigation (prefix allow) ---

SHELL_READONLY = [
    "ls -la",
    "cat README.md",
    "head -n 10 forge/runtime.py",
    "tail forge/runtime.py",
    "wc -l forge/runtime.py",
    "grep -n def forge/runtime.py",
    "rg classify_for_confirmation forge/",
    "file forge/runtime.py",
    "stat forge/runtime.py",
    "du -sh forge",
    "df -h",
    "pwd",
    "which pytest",
    "whereis git",
    "uname -a",
    "whoami",
    "id",
]

SHELL_READONLY_COMPOUND = [
    "ls && rm -rf /tmp/x",
    "cat README.md > /tmp/out",
    "grep foo file | something",
    "ls; rm x",
    "cat `echo hi`",
]

# Must stay unknown/PAUSE under pure prefix model (or rg --pre guard).
SHELL_STILL_UNKNOWN = [
    "find . -name '*.py'",
    "find . -delete",
    "find . -exec rm {} ;",
    "find . -exec rm {} +",
    "env rm -rf /tmp/x",
    "env FOO=1 rm x",
    "xargs rm",
    "sort -o out.txt in.txt",
    "sed -i 's/a/b/' file.py",
    "awk '{print}' file.py",
    "python -c 'print(1)'",
    "bash -c 'ls'",
    "sh -c 'ls'",
    "node -e '1'",
    "less README.md",
    "date",
    "rg --pre ./preprocessor pattern",
    "rg --pre=./preprocessor pattern",
    "rg --pre-glob '*.txt' --pre ./pp pattern",
]


def test_shell_readonly_class_and_gate_allow():
    for cmd in SHELL_READONLY:
        assert is_compound_shell_command(cmd) is False, cmd
        assert resolve_command_class(cmd) == "read_only", cmd
        assert resolve_run_command_gate(cmd) == ALLOW, cmd


def test_shell_readonly_compound_still_unknown_pause():
    for cmd in SHELL_READONLY_COMPOUND:
        assert resolve_command_class(cmd) == COMMAND_CLASS_UNKNOWN, cmd
        assert resolve_run_command_gate(cmd) == PAUSE, cmd


def test_dangerous_or_exec_shell_still_not_allow():
    for cmd in SHELL_STILL_UNKNOWN:
        assert resolve_run_command_gate(cmd) == PAUSE, cmd
        # Not classified as a confirmation-layer read_only allow path
        assert resolve_command_class(cmd) != "read_only", cmd


def test_rg_without_pre_still_read_only():
    assert resolve_command_class("rg foo") == "read_only"
    assert resolve_run_command_gate("rg foo bar") == ALLOW


def test_find_exec_delete_not_read_only():
    for cmd in (
        "find . -delete",
        "find . -exec rm {} ;",
        "find /tmp -execdir rm {} +",
    ):
        assert resolve_command_class(cmd) == COMMAND_CLASS_UNKNOWN, cmd
        assert resolve_run_command_gate(cmd) == PAUSE, cmd

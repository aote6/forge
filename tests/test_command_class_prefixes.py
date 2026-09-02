"""Tests for command_class prefix whitelist matching."""
from __future__ import annotations

from forge.command_class_prefixes import COMMAND_CLASS_PREFIXES, COMMAND_CLASS_UNKNOWN
from forge.constraint_enforcer import resolve_command_class


def test_longer_prefix_wins_python_pytest():
    assert resolve_command_class("python -m pytest -q") == "test"
    assert resolve_command_class("pytest tests/") == "test"


def test_git_push_is_vcs_write_not_partial():
    assert resolve_command_class("git push origin main") == "vcs_write"
    assert resolve_command_class("git status") == "vcs_read"
    assert resolve_command_class("git log --oneline") == "vcs_read"


def test_unknown_prefix_returns_unknown():
    assert resolve_command_class("unknown_binary --help") == COMMAND_CLASS_UNKNOWN
    assert resolve_command_class("") == COMMAND_CLASS_UNKNOWN
    assert resolve_command_class("gitignore") == COMMAND_CLASS_UNKNOWN  # not "git ..."


def test_destructive_prefixes():
    assert resolve_command_class("rm -rf /tmp/x") == "destructive"
    assert resolve_command_class("mv a b") == "destructive"


def test_prefixes_table_nonempty():
    assert "pytest" in COMMAND_CLASS_PREFIXES
    assert "python -m pytest" in COMMAND_CLASS_PREFIXES


def test_common_readonly_shell_prefixes():
    readonly = [
        "ls",
        "ls -la",
        "cat forge/runtime.py",
        "head -n 20 a.py",
        "tail -n 5 a.py",
        "wc -l a.py",
        "grep -n foo a.py",
        "rg pattern forge/",
        "file a.py",
        "stat a.py",
        "du -sh .",
        "df -h",
        "pwd",
        "which python3",
        "whereis ls",
        "uname -a",
        "whoami",
        "id",
    ]
    for cmd in readonly:
        assert resolve_command_class(cmd) == "read_only", cmd


def test_readonly_prefixes_in_table():
    for name in (
        "ls",
        "cat",
        "head",
        "tail",
        "wc",
        "grep",
        "rg",
        "file",
        "stat",
        "du",
        "df",
        "pwd",
        "which",
        "whereis",
        "uname",
        "whoami",
        "id",
    ):
        assert COMMAND_CLASS_PREFIXES.get(name) == "read_only", name

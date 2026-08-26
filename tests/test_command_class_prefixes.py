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

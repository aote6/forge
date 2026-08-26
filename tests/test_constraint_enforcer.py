"""Tests for forge.constraint_enforcer."""
from __future__ import annotations

from forge.constraint_enforcer import enforce, path_in_scope


def test_unregistered_tool_denied():
    d = enforce("no_such_tool_xyz", {}, None)
    assert d.allowed is False
    assert "unregistered" in d.reason


def test_not_allowed_machine_denies_write():
    d = enforce("write_file", {"path": "src/a.py"}, {"not_allowed": ["write"]})
    assert d.allowed is False
    assert "not_allowed" in d.reason


def test_not_allowed_advisory_allows_with_violation():
    d = enforce(
        "write_file",
        {"path": "src/a.py"},
        {"not_allowed": {"items": ["write"], "level": "advisory"}},
    )
    assert d.allowed is True
    assert any("not_allowed" in v for v in d.advisory_violations)


def test_scope_paths_whitelist():
    d = enforce(
        "read_file",
        {"path": "tests/x.py"},
        {"scope": {"paths": ["src/"]}},
    )
    assert d.allowed is False
    assert "scope.paths" in d.reason

    d2 = enforce(
        "read_file",
        {"path": "src/a.py"},
        {"scope": {"paths": ["src/"]}},
    )
    assert d2.allowed is True


def test_src2_does_not_match_src():
    assert path_in_scope("src2/main.py", ("src",)) is False
    assert path_in_scope("src/main.py", ("src",)) is True
    d = enforce(
        "read_file",
        {"path": "src2/main.py"},
        {"scope": {"paths": ["src"]}},
    )
    assert d.allowed is False


def test_apply_patch_denied_when_path_constraints_present():
    d = enforce(
        "apply_patch",
        {"patch": "..."},
        {"scope": {"paths": ["src/"]}},
    )
    assert d.allowed is False
    assert "unparseable" in d.reason.lower() or "scope.paths" in d.reason


def test_apply_patch_allowed_without_path_constraints():
    d = enforce("apply_patch", {"patch": "..."}, None)
    assert d.allowed is True


def test_command_class_unknown_denied_when_constraint_present():
    d = enforce(
        "run_command",
        {"cmd": "unknown_binary --help"},
        {"command_class": ["test"]},
    )
    assert d.allowed is False
    assert "command_class" in d.reason or "unknown" in d.reason


def test_command_class_test_allowed():
    d = enforce(
        "run_command",
        {"cmd": "pytest -q"},
        {"command_class": ["test"]},
    )
    assert d.allowed is True
    assert d.command_class == "test"


def test_not_allowed_checked_before_scope():
    """When both would fail, reason must be not_allowed (first in order)."""
    d = enforce(
        "write_file",
        {"path": "outside/x.py"},
        {
            "not_allowed": ["write"],
            "scope": {"paths": ["src/"]},
        },
    )
    assert d.allowed is False
    assert "not_allowed" in d.reason
    assert "scope.paths" not in d.reason

"""Layer B v1: workspace metadata manifest observation."""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from forge.adapters.base import ToolCall, ToolResult
from forge.agent_abi import AgentTask, STATUS_BLOCKED
from forge.execution_gate import VERIFY_SIDE_EFFECT_PATTERNS
from forge.subagent import run_subagent
from forge.workspace_manifest import (
    ManifestEntry,
    WORKSPACE_MANIFEST_EXCLUDE_DIR_NAMES,
    build_workspace_manifest,
    manifest_changed_paths,
    unauthorized_changed_paths,
)


def test_exclude_dir_names_include_required():
    required = {
        ".git",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".venv",
        "venv",
        "node_modules",
        ".forge",
    }
    assert required <= WORKSPACE_MANIFEST_EXCLUDE_DIR_NAMES


def test_manifest_new_modify_delete_file(tmp_path: Path):
    f = tmp_path / "a.txt"
    f.write_text("one")  # size 3
    before = build_workspace_manifest(tmp_path)
    assert "a.txt" in before
    assert before["a.txt"].kind == "file"

    f.write_text("two!!")  # size differs
    mid = build_workspace_manifest(tmp_path)
    changed = manifest_changed_paths(before, mid)
    assert "a.txt" in changed

    f.unlink()
    after = build_workspace_manifest(tmp_path)
    changed2 = manifest_changed_paths(mid, after)
    assert "a.txt" in changed2  # deletion

    (tmp_path / "b.txt").write_text("new")
    after2 = build_workspace_manifest(tmp_path)
    assert "b.txt" in manifest_changed_paths(after, after2)


def test_manifest_dir_create_delete(tmp_path: Path):
    before = build_workspace_manifest(tmp_path)
    d = tmp_path / "subdir"
    d.mkdir()
    (d / "x.txt").write_text("x")
    after = build_workspace_manifest(tmp_path)
    ch = manifest_changed_paths(before, after)
    assert "subdir" in ch
    assert "subdir/x.txt" in ch

    # delete dir tree
    (d / "x.txt").unlink()
    d.rmdir()
    after2 = build_workspace_manifest(tmp_path)
    ch2 = manifest_changed_paths(after, after2)
    assert "subdir" in ch2
    assert "subdir/x.txt" in ch2


def test_excluded_dirs_not_in_manifest(tmp_path: Path):
    for name in (".git", "__pycache__", ".pytest_cache", ".venv", "node_modules", ".forge"):
        p = tmp_path / name
        p.mkdir()
        (p / "noise").write_text("n")
    (tmp_path / "keep.py").write_text("ok")
    m = build_workspace_manifest(tmp_path)
    assert "keep.py" in m
    for name in (".git", "__pycache__", ".pytest_cache", ".venv", "node_modules", ".forge"):
        assert name not in m
        assert not any(k.startswith(name + "/") for k in m)


def test_symlink_not_followed(tmp_path: Path):
    outside = tmp_path / "outside_root_file"
    # put target outside by linking to /tmp style: create sibling outside scan root
    real_root = tmp_path / "root"
    real_root.mkdir()
    ext = tmp_path / "external"
    ext.mkdir()
    (ext / "secret.txt").write_text("secret")
    link = real_root / "linkdir"
    link.symlink_to(ext, target_is_directory=True)
    (real_root / "local.txt").write_text("local")
    m = build_workspace_manifest(real_root)
    assert "local.txt" in m
    assert "linkdir" in m
    assert m["linkdir"].kind == "symlink"
    # must not include external contents under linkdir/
    assert not any(k.startswith("linkdir/") for k in m)
    assert "secret.txt" not in m


def test_unauthorized_pattern_subtraction():
    before = {
        "forge/runtime.py": "old",
        ".pytest_cache/x": "old",
    }
    after = {
        "forge/runtime.py": "new",
        ".pytest_cache/x": "new",
    }
    patterns = VERIFY_SIDE_EFFECT_PATTERNS["run_test_structured"]
    rem = unauthorized_changed_paths(before, after, patterns)
    assert "forge/runtime.py" in rem
    assert ".pytest_cache/x" not in rem


def test_unauthorized_detects_deletion():
    before = {"gone.txt": "x"}
    after: dict = {}
    rem = unauthorized_changed_paths(before, after, frozenset())
    assert "gone.txt" in rem



class _ScriptedAdapter:
    def __init__(self, turns):
        self.turns = list(turns)
        self.i = 0

    def send(self, messages, schemas):
        if self.i < len(self.turns):
            content, tcs = self.turns[self.i]
            self.i += 1
            return MagicMock(content=content, tool_calls=tcs)
        return MagicMock(content="STOP_WHEN: met\nCONCLUSION:\ndone\n", tool_calls=None)


def _task(goal="t"):
    return AgentTask(goal=goal, max_steps=5)


def test_run_command_modify_file_caught_by_layer_b(tmp_path: Path):
    """run_command args have only cmd; workspace manifest must still observe the write."""
    target = tmp_path / "victim.txt"
    target.write_text("before\n")

    def run_command(cmd: str = "") -> ToolResult:
        target.write_text("pwned\n")
        return ToolResult.ok(display="ok")

    import forge.subagent as sa
    from forge.execution_gate import ALLOW

    orig = sa.classify_for_confirmation

    def _allow_run(name, args):
        if name == "run_command":
            return ALLOW
        return orig(name, args)

    adapter = _ScriptedAdapter(
        [
            (
                "STOP_WHEN: not_met",
                [
                    ToolCall(
                        id="1",
                        name="run_command",
                        arguments={"cmd": "echo pwned > victim.txt"},
                    )
                ],
            ),
        ]
    )
    try:
        sa.classify_for_confirmation = _allow_run  # type: ignore
        out = run_subagent(
            adapter,
            {"run_command": run_command},
            [{"name": "run_command", "parameters": {"type": "object", "properties": {}}}],
            _task(),
            project_root=tmp_path,
            confirm_fn=lambda _: True,
        )
    finally:
        sa.classify_for_confirmation = orig  # type: ignore

    assert out.status == STATUS_BLOCKED
    assert "unauthorized_world_change" in (out.status_reason or "")


def test_confirmed_write_not_flagged(tmp_path: Path):
    target = tmp_path / "a.py"
    target.write_text("OLD\n", encoding="utf-8")

    def do_replace(path, old_string, new_string, replace_all=False):
        p = Path(path)
        p.write_text(p.read_text().replace(old_string, new_string), encoding="utf-8")
        return ToolResult.ok(display="ok")

    adapter = _ScriptedAdapter(
        [
            (
                "STOP_WHEN: not_met",
                [
                    ToolCall(
                        id="1",
                        name="str_replace",
                        arguments={
                            "path": str(target),
                            "old_string": "OLD",
                            "new_string": "NEW",
                        },
                    )
                ],
            ),
            ("STOP_WHEN: met\nCONCLUSION:\nok\n", None),
        ]
    )
    out = run_subagent(
        adapter,
        {"str_replace": do_replace},
        [{"name": "str_replace", "parameters": {"type": "object", "properties": {}}}],
        _task(),
        project_root=tmp_path,
        confirm_fn=lambda _: True,
    )
    assert "unauthorized_world_change" not in (out.status_reason or "")
    assert target.read_text(encoding="utf-8") == "NEW\n"


def test_readonly_no_change_ok(tmp_path: Path):
    target = tmp_path / "a.txt"
    target.write_text("x")

    def read_file(path: str = "", start=1, end=0):
        return ToolResult.ok(display="x")

    adapter = _ScriptedAdapter(
        [
            (
                "STOP_WHEN: not_met",
                [ToolCall(id="1", name="read_file", arguments={"path": str(target)})],
            ),
            ("STOP_WHEN: met\nCONCLUSION:\nok\n", None),
        ]
    )
    out = run_subagent(
        adapter,
        {"read_file": read_file},
        [{"name": "read_file", "parameters": {"type": "object", "properties": {}}}],
        _task(),
        project_root=tmp_path,
    )
    assert "unauthorized_world_change" not in (out.status_reason or "")


def test_verify_engineering_change_via_manifest_unit(tmp_path: Path):
    src = tmp_path / "forge"
    src.mkdir()
    f = src / "runtime.py"
    f.write_text("old-content")
    before = build_workspace_manifest(tmp_path)
    f.write_text("new-content-longer")
    cache = tmp_path / ".pytest_cache"
    cache.mkdir()
    (cache / "x").write_text("c")
    after = build_workspace_manifest(tmp_path)
    patterns = VERIFY_SIDE_EFFECT_PATTERNS["run_test_structured"]
    rem = unauthorized_changed_paths(before, after, patterns)
    assert any(p.endswith("runtime.py") for p in rem)
    assert not any(".pytest_cache" in p for p in rem)

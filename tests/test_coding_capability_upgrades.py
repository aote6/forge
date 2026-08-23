"""Tests for coding-capability upgrades (symbol index, path resolve, tools)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from forge.core.symbol_index import (
    build_symbol_index,
    load_symbol_index,
    lookup_function_range,
    lookup_symbol,
)
from forge.tools.schemas import (
    MUTATION_TOOL_DECLARATIONS,
    MUTATION_TOOL_NAMES,
    READ_ONLY_TOOL_DECLARATIONS,
)
from forge.tools import make_tools
from forge.workspace import Workspace


def test_symbol_index_build_and_lookup(tmp_path: Path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "mod.py").write_text(
        "class Foo:\n    def bar(self):\n        return 1\n\nX = 1\n",
        encoding="utf-8",
    )
    data = build_symbol_index(tmp_path)
    assert (tmp_path / ".forge" / "symbols.json").is_file()
    assert "Foo" in data["symbols"]
    assert "bar" in data["symbols"]
    hits = lookup_symbol(tmp_path, "Foo")
    assert hits and hits[0]["kind"] == "class"
    assert hits[0]["path"] == "pkg/mod.py"
    rng = lookup_function_range(tmp_path, "pkg/mod.py", "bar")
    assert rng is not None
    assert rng[0] <= rng[1]


def test_symbol_index_reload_uses_cache(tmp_path: Path):
    (tmp_path / "a.py").write_text("def alpha():\n    pass\n", encoding="utf-8")
    build_symbol_index(tmp_path)
    data = load_symbol_index(tmp_path, rebuild_if_stale=True)
    assert "alpha" in data["symbols"]


def test_schemas_include_new_tools():
    ro = {d["name"] for d in READ_ONLY_TOOL_DECLARATIONS}
    mu = {d["name"] for d in MUTATION_TOOL_DECLARATIONS}
    for name in ("read_function", "run_type_check", "resolve_path_object", "glob_files"):
        assert name in ro, name
    assert "edit_files_batch" in mu
    assert "str_replace" in MUTATION_TOOL_NAMES
    assert "write_file" in MUTATION_TOOL_NAMES
    mod = next(d for d in MUTATION_TOOL_DECLARATIONS if d["name"] == "modify_file")
    req = mod["parameters"].get("required") or []
    assert "object_id" not in req
    assert "path" in req and "operations" in req


def test_make_tools_registers_coding_tools(tmp_path: Path):
    ws = Workspace(project_root=str(tmp_path))
    tools = make_tools(workspace=ws, allow_mutation=False)
    assert "find_symbol_definition" in tools
    assert "read_function" in tools
    assert "run_type_check" in tools
    assert "rebuild_symbol_index" in tools
    (tmp_path / "x.py").write_text("def hello():\n    return 42\n", encoding="utf-8")
    r = tools["rebuild_symbol_index"]()
    assert r.success
    r2 = tools["find_symbol_definition"]("hello")
    assert r2.success
    assert "x.py" in r2.display
    r3 = tools["read_function"]("x.py", "hello")
    assert r3.success
    assert "return 42" in r3.display
    r4 = tools["run_type_check"]("x.py", "ast")
    assert r4.success


def test_run_type_check_ast_detects_mismatch(tmp_path: Path):
    ws = Workspace(project_root=str(tmp_path))
    tools = make_tools(workspace=ws, allow_mutation=False)
    (tmp_path / "bad.py").write_text("x: int = 'nope'\n", encoding="utf-8")
    r = tools["run_type_check"]("bad.py", "ast")
    assert not r.success
    assert "annotated int" in r.display


def test_conversation_log_helper(tmp_path: Path):
    from forge.runtime import _append_conversation_log

    _append_conversation_log(str(tmp_path), "user", "hello task")
    log = tmp_path / ".forge" / "conversation_log.jsonl"
    assert log.is_file()
    line = log.read_text(encoding="utf-8").strip().splitlines()[0]
    rec = json.loads(line)
    assert rec["role"] == "user"
    assert "hello" in rec["content"]


def test_modify_file_schema_object_id_optional():
    mod = next(d for d in MUTATION_TOOL_DECLARATIONS if d["name"] == "modify_file")
    assert set(mod["parameters"].get("required") or []) == {"path", "operations"}

"""Product experience: auto-register, todo, apply_patch, NEXT hints, web_fetch."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from forge.tools.patch_utils import apply_unified_patch_to_files, parse_unified_diff
from forge.tools.schemas import MUTATION_TOOL_DECLARATIONS, READ_ONLY_TOOL_DECLARATIONS
from forge.tools import make_tools
from forge.workspace import Workspace


def test_schemas_include_product_tools():
    from forge.tools.schemas import CONTROL_PLANE_TOOLS

    ro = {d["name"] for d in READ_ONLY_TOOL_DECLARATIONS}
    mu = {d["name"] for d in MUTATION_TOOL_DECLARATIONS}
    assert "web_fetch" in ro
    assert "todo_write" in CONTROL_PLANE_TOOLS and "todo_list" in CONTROL_PLANE_TOOLS
    assert "todo_write" not in ro
    assert "apply_patch" in mu


def test_parse_unified_diff_simple():
    patch = """--- a/foo.py
+++ b/foo.py
@@ -1,3 +1,3 @@
 line1
-line2
+line2b
 line3
"""
    files = parse_unified_diff(patch)
    assert len(files) == 1
    assert files[0]["path"] == "foo.py"
    assert len(files[0]["hunks"]) == 1


def test_apply_unified_patch_to_files(tmp_path: Path):
    f = tmp_path / "foo.py"
    f.write_text("line1\nline2\nline3\n", encoding="utf-8")
    patch = """--- a/foo.py
+++ b/foo.py
@@ -1,3 +1,3 @@
 line1
-line2
+line2b
 line3
"""
    plan = apply_unified_patch_to_files(str(tmp_path), patch)
    assert "error" not in plan or plan.get("error") is None
    assert plan["files"][0]["new_content"] == "line1\nline2b\nline3\n"


def test_todo_write_and_list(tmp_path: Path):
    ws = Workspace(project_root=str(tmp_path))
    tools = make_tools(workspace=ws, allow_mutation=False)
    r = tools["todo_write"](
        items=[
            {"id": "1", "content": "read code", "status": "done"},
            {"id": "2", "content": "edit", "status": "in_progress"},
            {"content": "test", "status": "pending"},
        ]
    )
    assert r.success
    assert "[x]" in r.display and "[~]" in r.display
    r2 = tools["todo_list"]()
    assert r2.success
    assert len(r2.payload["todos"]) == 3


def test_web_fetch_rejects_non_http():
    ws = Workspace(project_root=".")
    tools = make_tools(workspace=ws, allow_mutation=False)
    r = tools["web_fetch"]("file:///etc/passwd")
    assert not r.success
    assert "http" in r.display.lower()


def test_next_hint_helper():
    from forge.tools.intent_tools import _attach_next, _next_hint
    from forge.adapters.base import ToolResult

    assert "NEXT:" in _next_hint(["a.py"])
    tr = ToolResult.ok(display="RESULT: ok", payload={})
    out = _attach_next(tr, ["a.py"])
    assert "NEXT:" in out.display
    assert "git_diff" in out.display or "run_test" in out.display


def test_auto_register_resolve_helpers(tmp_path: Path):
    """Unit-level: path norm + resolve with path map."""
    from forge.tools.intent_tools import _norm_path, _resolve_oid
    from forge.projections.object_path import ObjectPathMap

    assert _norm_path("./src/x.py") == "src/x.py"
    world = MagicMock()
    pm = ObjectPathMap()
    pm.set(42, "src/x.py")
    world._path_map = pm
    world.find_object_id_for_path = MagicMock(return_value=None)
    world.find_object_id = MagicMock(return_value=None)
    assert _resolve_oid(world, "src/x.py", None) == 42
    assert _resolve_oid(world, "./src/x.py", None) == 42


def test_make_tools_registers_apply_patch_and_todo(tmp_path: Path):
    ws = Workspace(project_root=str(tmp_path))
    tools = make_tools(workspace=ws, allow_mutation=False)
    assert "todo_write" in tools and "web_fetch" in tools
    # apply_patch only with mutation+world
    tools2 = make_tools(
        workspace=ws, world_runtime=None, projections=None, allow_mutation=True
    )
    # without world, mutation tools not registered
    assert "apply_patch" not in tools2

"""Quality-focused tool surface: curated schemas + new edit primitives."""
from __future__ import annotations

from pathlib import Path

from forge.tools.schemas import (
    READ_ONLY_TOOL_DECLARATIONS,
    MUTATION_TOOL_DECLARATIONS,
    MUTATION_TOOL_NAMES,
)
from forge.tools import make_tools
from forge.workspace import Workspace

# Target: lean LLM surface (not the old 40-tool dump)
CORE_READ = {
    "read_file", "read_function", "glob_files", "search_code",
    "find_symbol_definition", "get_repo_map", "git_diff",
    "run_command", "run_test_structured", "run_type_check",
    "world_info", "list_world_objects", "get_world_object", "list_world_links",
    "search_history", "resolve_path_object",
}
CORE_MUT = {
    "str_replace", "write_file", "create_file", "modify_file",
    "edit_files_batch", "delete_file", "create_object", "link_objects", "unlink_objects",
}


def test_llm_surface_is_curated_not_bloated():
    from forge.tools.schemas import CONTROL_PLANE_TOOLS

    ro = {d["name"] for d in READ_ONLY_TOOL_DECLARATIONS}
    mu = {d["name"] for d in MUTATION_TOOL_DECLARATIONS}
    assert "glob_files" in ro and "web_fetch" in ro
    assert "todo_write" in CONTROL_PLANE_TOOLS
    assert "todo_write" not in ro
    assert "str_replace" in mu and "apply_patch" in mu and "write_file" in mu
    assert len(ro) + len(mu) + len(CONTROL_PLANE_TOOLS) <= 44
    # legacy noise must not be on the LLM schema list
    for noise in (
        "get_call_chain", "summarize_file", "extract_code_skeleton",
        "get_context_budget", "preview_line_mutation", "read_file_with_lines",
        "git_status_enhanced", "list_tests", "read_git_version",
    ):
        assert noise not in ro and noise not in mu


def test_str_replace_and_write_file_on_schema():
    assert "str_replace" in MUTATION_TOOL_NAMES
    assert "write_file" in MUTATION_TOOL_NAMES
    sr = next(d for d in MUTATION_TOOL_DECLARATIONS if d["name"] == "str_replace")
    assert set(sr["parameters"]["required"]) == {"path", "old_string", "new_string"}


def test_glob_files_tool(tmp_path: Path):
    (tmp_path / "a.py").write_text("x=1\n", encoding="utf-8")
    (tmp_path / "b.txt").write_text("y\n", encoding="utf-8")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "c.py").write_text("z=1\n", encoding="utf-8")
    ws = Workspace(project_root=str(tmp_path))
    tools = make_tools(workspace=ws, allow_mutation=False)
    assert "glob_files" in tools
    r = tools["glob_files"]("**/*.py")
    assert r.success
    assert r.payload["count"] >= 2
    assert any(f.endswith("a.py") for f in r.payload["files"])


def test_implementations_still_register_legacy_helpers(tmp_path: Path):
    """Implementations stay for tests/compat; only schemas are curated."""
    ws = Workspace(project_root=str(tmp_path))
    tools = make_tools(workspace=ws, allow_mutation=False)
    assert "summarize_file" in tools  # still callable if needed
    assert "find_symbol_definition" in tools



def test_glob_files_hides_forge_by_default(tmp_path: Path):
    """Wide patterns must not surface .forge runtime artifacts."""
    (tmp_path / "src.py").write_text("x=1\n", encoding="utf-8")
    forge_dir = tmp_path / ".forge"
    forge_dir.mkdir()
    (forge_dir / "last_test_result.json").write_text('{"ok": true}\n', encoding="utf-8")
    (forge_dir / "runtime_state.json").write_text("{}\n", encoding="utf-8")
    ws = Workspace(project_root=str(tmp_path))
    tools = make_tools(workspace=ws, allow_mutation=False)
    for pat in ("**/*", "**/*.json", "**/.forge/**"):
        r = tools["glob_files"](pat)
        assert r.success, pat
        files = r.payload["files"]
        assert not any(f.replace("\\", "/").startswith(".forge/") for f in files), (
            pat,
            files,
        )
    r_py = tools["glob_files"]("**/*.py")
    assert r_py.success
    assert any(f.endswith("src.py") for f in r_py.payload["files"])


def test_glob_files_explicit_forge_pattern_visible(tmp_path: Path):
    """Explicit .forge/... patterns must see files that exist on disk."""
    forge_dir = tmp_path / ".forge"
    forge_dir.mkdir()
    target = forge_dir / "last_test_result.json"
    target.write_text('{"passed": 1}\n', encoding="utf-8")
    (tmp_path / "app.py").write_text("y=2\n", encoding="utf-8")
    ws = Workspace(project_root=str(tmp_path))
    tools = make_tools(workspace=ws, allow_mutation=False)

    r = tools["glob_files"](".forge/last_test_result.json")
    assert r.success
    assert r.payload["count"] >= 1
    assert any(
        f.replace("\\", "/").endswith("last_test_result.json") for f in r.payload["files"]
    )

    r2 = tools["glob_files"](".forge/*")
    assert r2.success
    assert r2.payload["count"] >= 1
    assert any(".forge/" in f.replace("\\", "/") for f in r2.payload["files"])

    # Non-explicit wide glob still hides the same file.
    r3 = tools["glob_files"]("**/*.json")
    assert r3.success
    assert not any(
        "last_test_result.json" in f.replace("\\", "/") for f in r3.payload["files"]
    )

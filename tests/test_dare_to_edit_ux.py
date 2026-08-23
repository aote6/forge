"""Dare-to-edit UX: undo, outline, error slices, display blocks, memory."""
from __future__ import annotations

from pathlib import Path

from forge.tools.display import format_block, error_slices, snippet_around
from forge.tools.tx_shadow import record_tx, undo_last
from forge.tools.project_memory import update_memory, load_memory, format_for_prompt
from forge.tools import make_tools
from forge.workspace import Workspace
from forge.tools.schemas import MUTATION_TOOL_NAMES, READ_ONLY_TOOL_DECLARATIONS


def test_format_block_mobile_friendly():
    s = format_block("str_replace", "OK", {"path": "a.py", "tx": 1}, "body here", hint="undo", clip={"undo": "undo_last_tx()"})
    assert s.startswith("=== FORGE/str_replace ===")
    assert "STATUS: OK" in s
    assert "--- END FORGE ---" in s
    assert "=== FORGE/CLIP ===" in s


def test_error_slices_finds_traceback():
    text = "ok\n" * 5 + "Traceback (most recent call last):\n  File x\nError: boom\n" + "tail\n" * 5
    s = error_slices(text, window=3)
    assert "Traceback" in s or "Error" in s


def test_shadow_undo(tmp_path: Path):
    f = tmp_path / "a.py"
    f.write_text("v1\n", encoding="utf-8")
    record_tx(str(tmp_path), tx_id=10, version=1, files={"a.py": "v1\n"})
    f.write_text("v2\n", encoding="utf-8")
    info = undo_last(str(tmp_path))
    assert info["ok"]
    assert f.read_text(encoding="utf-8") == "v1\n"


def test_project_memory(tmp_path: Path):
    update_memory(str(tmp_path), test_command="pytest -q", recent_files="a.py", last_task="fix")
    data = load_memory(str(tmp_path))
    assert data["test_command"] == "pytest -q"
    assert "a.py" in data["recent_files"]
    text = format_for_prompt(str(tmp_path))
    assert "项目记忆" in text


def test_read_file_outline(tmp_path: Path):
    body = "\n".join([f"def f{i}():\n    return {i}\n" for i in range(30)])
    # make >150 lines
    body = body + "\n".join(f"# pad {i}" for i in range(100))
    (tmp_path / "big.py").write_text(body, encoding="utf-8")
    ws = Workspace(project_root=str(tmp_path))
    tools = make_tools(workspace=ws, allow_mutation=False)
    r = tools["read_file"]("big.py")
    assert r.success
    assert r.payload.get("mode") == "outline"
    assert "FORGE/read_file" in r.display


def test_undo_on_schema():
    assert "undo_last_tx" in MUTATION_TOOL_NAMES
    names = {d["name"] for d in READ_ONLY_TOOL_DECLARATIONS}
    assert "project_memory" in names


def test_snippet_around():
    s = snippet_around("a\nb\nc\nd\ne\n", max_lines=3)
    assert "a" in s

"""Engineering UX: auto-diff, failure_context, session history helpers."""
from __future__ import annotations

from pathlib import Path

from forge.tools.intent_tools import _make_unified_diff, _attach_diff
from forge.adapters.base import ToolResult
from forge.runtime import _load_session_summary, _save_session_summary
from forge.tools import make_tools
from forge.workspace import Workspace


def test_make_unified_diff_simple():
    d = _make_unified_diff("a.py", "x = 1\n", "x = 2\n")
    assert "a.py" in d
    assert "-x = 1" in d or "-x = 1\n" in d
    assert "+x = 2" in d or "+x = 2\n" in d


def test_attach_diff_before_next():
    r = ToolResult.ok(display="RESULT: path=a.py\nok\nNEXT: verify", payload={})
    out = _attach_diff(r, "a.py", "old\n", "new\n")
    assert "DIFF:" in out.display
    assert "NEXT:" in out.display
    assert out.display.index("DIFF:") < out.display.index("NEXT:")
    assert "diff" in out.payload


def test_session_and_history_load(tmp_path: Path):
    forge = tmp_path / ".forge"
    forge.mkdir()
    (forge / "conversation_history.json").write_text(
        '{"notes":["fixed bug"],"summary":{"last_tasks":["fix tests"],"last_conclusions":["fixed bug"]}}',
        encoding="utf-8",
    )
    text = _load_session_summary(str(tmp_path))
    assert "上次会话摘要" in text
    assert "fix tests" in text or "fixed bug" in text


def test_run_test_structured_has_failure_context_field(tmp_path: Path):
    """When tests pass, failure_context is empty list; field always present."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_ok.py").write_text(
        "def test_ok():\n    assert True\n", encoding="utf-8"
    )
    ws = Workspace(project_root=str(tmp_path))
    tools, _, _ = make_tools(workspace=ws, allow_mutation=False)
    r = tools["run_test_structured"]("tests/")
    assert "failure_context" in r.payload
    assert r.payload["returncode"] == 0
    assert r.display.startswith("RESULT:")


def test_install_sh_exists():
    root = Path(__file__).resolve().parents[1]
    assert (root / "install.sh").is_file()

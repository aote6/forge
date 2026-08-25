"""run_single_test must not invoke a shell (no shell=True / string cmd)."""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from forge.tools.test_tools import make_test_tools
from forge.workspace import Workspace


def test_run_single_test_uses_argv_and_shell_false(tmp_path: Path):
    """subprocess.run must receive a list argv with shell=False."""
    ws = Workspace(project_root=str(tmp_path))
    tools = make_test_tools(ws)
    run_single_test = tools["run_single_test"]

    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs

        class R:
            returncode = 0
            stdout = "ok\n"
            stderr = ""

        return R()

    with patch("subprocess.run", side_effect=fake_run):
        result = run_single_test("tests/test_example.py")

    assert isinstance(captured["cmd"], list), "cmd must be argv list, not shell string"
    assert captured["kwargs"].get("shell") is False
    assert captured["cmd"][:3] == ["python3", "-m", "pytest"]
    assert captured["cmd"][3] == "tests/test_example.py"
    assert "-v" in captured["cmd"]
    assert "--tb=short" in captured["cmd"]
    assert result.success is True
    assert "python3 -m pytest tests/test_example.py" in (result.display or "")


def test_run_single_test_metacharacters_are_one_argv(tmp_path: Path):
    """Shell metacharacters in path stay a single argv element (not extra commands)."""
    ws = Workspace(project_root=str(tmp_path))
    tools = make_test_tools(ws)
    run_single_test = tools["run_single_test"]

    evil = "tests/x.py; echo PWNED"
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs

        class R:
            returncode = 1
            stdout = ""
            stderr = "file not found\n"

        return R()

    with patch("subprocess.run", side_effect=fake_run):
        result = run_single_test(evil)

    assert isinstance(captured["cmd"], list)
    assert captured["kwargs"].get("shell") is False
    # Entire evil string is one element — shell would have split on ';'
    assert captured["cmd"][3] == evil
    assert "PWNED" not in captured["cmd"]  # not expanded into separate argv
    assert result.success is False


def test_run_single_test_no_shell_string_form_in_source():
    """Regression: source must not pass a shell command string to subprocess.run."""
    src = Path(__file__).resolve().parents[1] / "forge" / "tools" / "test_tools.py"
    text = src.read_text(encoding="utf-8")
    # Locate run_single_test body only
    start = text.index("def run_single_test")
    end = text.index("return {", start)
    body = text[start:end]
    assert "shell=True" not in body
    assert 'f"python3 -m pytest {path}' not in body
    assert "shell=False" in body

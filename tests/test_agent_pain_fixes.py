"""Agent pain-point fixes: related tests, cache, near-miss, veritas errors, compress."""
from __future__ import annotations

from pathlib import Path

from forge.tools.related_tests import find_related_tests, format_related_hint
from forge.tools.read_cache import put, get, invalidate, clear
from forge.tools.near_miss import find_near_misses
from forge.tools.errors import classify_error, decorate_fail_message
from forge.runtime import _compress_messages
from forge.adapters.base import Message
from forge.tools import make_tools
from forge.workspace import Workspace


def test_related_tests_by_convention(tmp_path: Path):
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "session.py").write_text("def f():\n    pass\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_session.py").write_text(
        "from pkg.session import f\n\ndef test_f():\n    assert True\n",
        encoding="utf-8",
    )
    found = find_related_tests(str(tmp_path), "pkg/session.py")
    assert any("test_session" in f for f in found)
    hint = format_related_hint(str(tmp_path), "pkg/session.py")
    assert "RELATED_TESTS" in hint


def test_read_cache_hit_and_invalidate(tmp_path: Path):
    clear()
    f = tmp_path / "a.py"
    f.write_text("hello\n", encoding="utf-8")
    put(str(tmp_path), "a.py", "hello\n")
    hit = get(str(tmp_path), "a.py")
    assert hit is not None
    assert hit[0] == "hello\n"
    invalidate(str(tmp_path), "a.py")
    assert get(str(tmp_path), "a.py") is None


def test_near_miss_finds_similar():
    text = "def foo():\n    return 1\n\ndef bar():\n    return 2\n"
    misses = find_near_misses(text, "def foo():\n    return 0")
    assert misses


def test_veritas_error_classify():
    info = classify_error("Connection refused to veritasd")
    assert info["kind"] == "veritasd_offline"
    msg = decorate_fail_message("failed", "connection refused")
    assert "VERITAS" in msg or "veritasd" in msg.lower()


def test_compress_messages():
    msgs = [Message(role="system", content="s")]
    for i in range(30):
        msgs.append(Message(role="tool", content=f"RESULT big payload {i}\n" + ("x" * 100), name="read_file"))
    out = _compress_messages(msgs, keep_recent_tools=4)
    tool = [m for m in out if m.role == "tool"]
    assert any("compressed" in (m.content or "") for m in tool)


def test_read_file_uses_cache(tmp_path: Path):
    clear()
    (tmp_path / "x.py").write_text("def a():\n    return 1\n", encoding="utf-8")
    ws = Workspace(project_root=str(tmp_path))
    tools, _, _ = make_tools(workspace=ws, allow_mutation=False)
    r1 = tools["read_file"]("x.py")
    assert r1.success
    r2 = tools["read_file"]("x.py")
    assert r2.success

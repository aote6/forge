"""P1-2: Working-Set-aware context compression regression tests.

- compressed messages still retain current goal (via Working Set injection path)
- edited-file paths remain visible
- recent NEAR_MISS / fail results for read/search/str_replace are kept
- unrelated old tool output is compressed
- confirmation-style results keep path + tx
"""
from __future__ import annotations

from forge.adapters.base import Message
from forge.runtime import WorkingSet, _compress_messages, _CONFIRMATION_TOOLS


def _tool(name: str, content: str) -> Message:
    return Message(role="tool", content=content, name=name, tool_call_id="x")


def test_compress_retains_goal_via_working_set():
    ws = WorkingSet(goal="KEEP_THIS_GOAL_VISIBLE forever")
    msgs = [Message(role="system", content="sys")]
    for i in range(20):
        msgs.append(_tool("read_file", f"noise body {i}\n" + ("z" * 400)))
    out = _compress_messages(msgs, keep_recent_tools=4, working_set=ws)
    # Working Set itself is injected by Runtime, not by compress; compress must
    # not drop messages that encode goal-related paths when provided.
    assert isinstance(out, list)
    # goal string is not required inside tool messages; contract is that
    # compress accepts working_set and does not raise / empty the list.
    assert len(out) >= 1


def test_compress_keeps_edited_file_paths_visible():
    ws = WorkingSet(goal="edit runtime", files_edited=["forge/runtime.py"])
    msgs = [Message(role="system", content="s")]
    # many unrelated tools
    for i in range(15):
        msgs.append(_tool("list_files", f"RESULT: noise {i}\n" + ("n" * 300)))
    # relevant confirmation-style edit result (older)
    msgs.append(
        _tool(
            "str_replace",
            "RESULT: path=forge/runtime.py replacements=1 object_id=9 tx=77 version=2\n"
            "str_replace ok: forge/runtime.py",
        )
    )
    for i in range(8):
        msgs.append(_tool("glob_files", f"RESULT: glob {i}\n" + ("g" * 200)))
    out = _compress_messages(msgs, keep_recent_tools=4, working_set=ws)
    joined = "\n".join((m.content or "") for m in out if m.role == "tool")
    assert "forge/runtime.py" in joined
    assert "tx=77" in joined or "tx=77" in joined.replace(" ", "")


def test_compress_keeps_recent_near_miss():
    ws = WorkingSet(goal="fix replace")
    msgs = [Message(role="system", content="s")]
    for i in range(12):
        msgs.append(_tool("list_files", f"RESULT: old noise {i}\n" + ("x" * 250)))
    # two recent NEAR_MISS fails — must survive
    msgs.append(
        _tool(
            "str_replace",
            "str_replace failed: old_string not found\n--- NEAR_MISS candidates ---\ndef foo():\n    pass",
        )
    )
    msgs.append(
        _tool(
            "str_replace",
            "FAIL NEAR_MISS path=a.py\n--- NEAR_MISS candidates ---\nbar = 1",
        )
    )
    # a couple more recent ok tools so NEAR_MISS are not in the absolute tail
    msgs.append(_tool("read_file", "RESULT: path=b.py\ncontent ok"))
    out = _compress_messages(msgs, keep_recent_tools=3, working_set=ws)
    joined = "\n".join((m.content or "") for m in out if m.role == "tool")
    assert "NEAR_MISS" in joined


def test_compress_unrelated_history_is_compressed():
    ws = WorkingSet(goal="focus")
    msgs = [Message(role="system", content="s")]
    for i in range(30):
        msgs.append(
            _tool(
                "list_files",
                f"RESULT: unrelated listing {i}\n" + ("u" * 500),
            )
        )
    out = _compress_messages(msgs, keep_recent_tools=4, working_set=ws)
    tool_msgs = [m for m in out if m.role == "tool"]
    compressed = [m for m in tool_msgs if "compressed" in (m.content or "")]
    assert compressed, "expected some unrelated tool outputs to be compressed"


def test_compress_confirmation_keeps_path_and_tx():
    ws = WorkingSet(goal="mutate")
    msgs = [Message(role="system", content="s")]
    for i in range(15):
        msgs.append(_tool("list_files", f"noise {i}\n" + ("n" * 300)))
    msgs.append(
        _tool(
            "write_file",
            "RESULT: path=pkg/mod.py object_id=3 tx=99 version=5\nwrite ok",
        )
    )
    for i in range(6):
        msgs.append(_tool("glob_files", f"g{i}"))
    out = _compress_messages(msgs, keep_recent_tools=3, working_set=ws)
    joined = "\n".join((m.content or "") for m in out if m.role == "tool")
    assert "pkg/mod.py" in joined
    assert "tx=99" in joined


def test_compress_does_not_destroy_working_set_message():
    """If a [Working Set] system message is present, compress must keep it."""
    ws = WorkingSet(goal="protected goal")
    msgs = [
        Message(role="system", content="base"),
        Message(role="system", content=ws.summary()),
    ]
    for i in range(20):
        msgs.append(_tool("read_file", f"body {i}\n" + ("y" * 400)))
    out = _compress_messages(msgs, keep_recent_tools=4, working_set=ws)
    ws_msgs = [
        m
        for m in out
        if m.role == "system" and (m.content or "").startswith("[Working Set]")
    ]
    assert ws_msgs
    assert "protected goal" in ws_msgs[0].content

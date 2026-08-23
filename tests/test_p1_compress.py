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
    ws_summary = ws.summary()
    msgs = [
        Message(role="system", content="base"),
        Message(role="system", content=ws_summary),
    ]
    for i in range(25):
        msgs.append(_tool("read_file", f"noise body {i}\n" + ("z" * 400)))
    out = _compress_messages(msgs, keep_recent_tools=4, working_set=ws)
    # 必须真的触发压缩（消息数 >= 24，工具数 > keep_recent_tools）……
    tool_msgs = [m for m in out if m.role == "tool"]
    assert any("compressed" in (m.content or "") for m in tool_msgs)
    # ……但承载 goal 的 [Working Set] system 消息必须原样保留，不能丢、不能改。
    ws_msgs = [
        m
        for m in out
        if m.role == "system" and (m.content or "").startswith("[Working Set]")
    ]
    assert len(ws_msgs) == 1
    assert ws_msgs[0].content == ws_summary
    assert "KEEP_THIS_GOAL_VISIBLE forever" in ws_msgs[0].content


def test_compress_keeps_edited_file_paths_visible():
    ws = WorkingSet(goal="edit runtime", files_edited=["forge/runtime.py"])
    msgs = [Message(role="system", content="s")]
    # many unrelated tools
    for i in range(15):
        msgs.append(_tool("list_files", f"RESULT: noise {i}\n" + ("n" * 300)))
    # relevant confirmation-style edit result (older)
    edit_result = (
        "RESULT: path=forge/runtime.py replacements=1 object_id=9 tx=77 version=2\n"
        "str_replace ok: forge/runtime.py"
    )
    msgs.append(_tool("str_replace", edit_result))
    for i in range(8):
        msgs.append(_tool("glob_files", f"RESULT: glob {i}\n" + ("g" * 200)))
    out = _compress_messages(msgs, keep_recent_tools=4, working_set=ws)
    tool_msgs = [m for m in out if m.role == "tool"]
    # 必须真的触发了压缩：存在被摘要的无关消息，否则"保留 path"无从谈起。
    assert any((m.content or "").startswith("[compressed") for m in tool_msgs)
    # 承载 files_edited path 的 str_replace 结果逐字保留：不压缩、不改写、不截断。
    str_replace_msgs = [m for m in tool_msgs if m.name == "str_replace"]
    assert len(str_replace_msgs) == 1
    assert str_replace_msgs[0].content == edit_result


def test_compress_keeps_recent_near_miss():
    ws = WorkingSet(goal="fix replace")
    msgs = [Message(role="system", content="s")]
    for i in range(20):
        msgs.append(_tool("list_files", f"RESULT: old noise {i}\n" + ("x" * 250)))
    near_miss_a = (
        "str_replace failed: old_string not found\n"
        "--- NEAR_MISS candidates ---\ndef foo():\n    pass"
    )
    near_miss_b = "FAIL NEAR_MISS path=a.py\n--- NEAR_MISS candidates ---\nbar = 1"
    msgs.append(_tool("str_replace", near_miss_a))
    msgs.append(_tool("str_replace", near_miss_b))
    # 足够多的后续工具，让两条 NEAR_MISS 落到 keep_recent_tools 尾巴之外，
    # 此时只有「最近 2 条失败结果不压」这条规则能保住它们。
    for i in range(6):
        msgs.append(_tool("glob_files", f"RESULT: recent glob {i}\n" + ("g" * 100)))
    out = _compress_messages(msgs, keep_recent_tools=3, working_set=ws)
    contents = [(m.content or "") for m in out if m.role == "tool"]
    # 两条 NEAR_MISS 必须逐字保留（不是被摘要掉）
    assert near_miss_a in contents
    assert near_miss_b in contents
    # 无关旧内容确实被压缩了（否则本测试根本没走到压缩路径）
    assert any("compressed" in c for c in contents)


def test_compress_unrelated_history_is_compressed():
    ws = WorkingSet(goal="focus")
    msgs = [Message(role="system", content="s")]
    originals = []
    for i in range(30):
        m = _tool("list_files", f"RESULT: unrelated listing {i}\n" + ("u" * 500))
        originals.append(m)
        msgs.append(m)
    out = _compress_messages(msgs, keep_recent_tools=4, working_set=ws)
    tool_msgs = [m for m in out if m.role == "tool"]
    # 30 条工具：最旧 26 条被摘要，最近 4 条逐字保留
    assert len(tool_msgs) == 30
    compressed = tool_msgs[:26]
    kept = tool_msgs[26:]
    assert all((m.content or "").startswith("[compressed") for m in compressed)
    assert [m.content for m in kept] == [m.content for m in originals[-4:]]


def test_compress_confirmation_keeps_path_and_tx():
    ws = WorkingSet(goal="mutate")
    msgs = [Message(role="system", content="s")]
    for i in range(20):
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
    tool_msgs = [m for m in out if m.role == "tool"]
    # write_file 是旧的确认型结果 → 被压缩，但摘要必须保留 path + tx
    compressed_write = [m for m in tool_msgs if m.name == "write_file"]
    assert len(compressed_write) == 1
    summary = compressed_write[0].content or ""
    assert summary.startswith("[compressed FACT write_file]")
    assert "pkg/mod.py" in summary
    assert "tx=99" in summary


def test_compress_does_not_destroy_working_set_message():
    """If a [Working Set] system message is present, compress must keep it verbatim."""
    ws = WorkingSet(goal="protected goal")
    ws_summary = ws.summary()
    msgs = [
        Message(role="system", content="base"),
        Message(role="system", content=ws_summary),
    ]
    # 30 条工具消息（>= 24）确保真正触发压缩；<24 会提前 return，测不到压缩路径。
    for i in range(30):
        msgs.append(_tool("read_file", f"body {i}\n" + ("y" * 400)))
    out = _compress_messages(msgs, keep_recent_tools=4, working_set=ws)
    # 必须真的触发了压缩：存在被摘要的工具消息。
    tool_msgs = [m for m in out if m.role == "tool"]
    assert any((m.content or "").startswith("[compressed") for m in tool_msgs)
    # [Working Set] system 消息逐字保留：数量、内容都不变。
    ws_msgs = [
        m
        for m in out
        if m.role == "system" and (m.content or "").startswith("[Working Set]")
    ]
    assert len(ws_msgs) == 1
    assert ws_msgs[0].content == ws_summary
    assert "protected goal" in ws_msgs[0].content

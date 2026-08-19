from pathlib import Path

ROOT = Path.home() / "forge"

def patch(rel_path, old, new, label):
    fp = ROOT / rel_path
    text = fp.read_text(encoding="utf-8")
    n = text.count(old)
    if n != 1:
        print(f"[SKIP] {label}: 匹配到 {n} 处（需要恰好1处），未修改。请手动检查 {rel_path}")
        return False
    fp.write_text(text.replace(old, new, 1), encoding="utf-8")
    print(f"[OK] {label}")
    return True

patch(
    "forge/runtime.py",
    '''def _compress_messages(messages: list, keep_recent_tools: int = 6) -> list:
    """Replace older tool results with one-line summaries to curb context rot."""
    if len(messages) < 24:
        return messages
    tool_idxs = [i for i, m in enumerate(messages) if getattr(m, "role", None) == "tool"]
    if len(tool_idxs) <= keep_recent_tools:
        return messages
    drop = set(tool_idxs[:-keep_recent_tools])
    out = []
    for i, m in enumerate(messages):
        if i in drop:
            name = getattr(m, "name", None) or "tool"
            content = (getattr(m, "content", None) or "")
            first = content.strip().splitlines()[0][:120] if content.strip() else ""
            summary = f"[compressed FACT {name}] {first}"
            try:
                from forge.adapters.base import Message as ForgeMessage
                out.append(ForgeMessage(role="tool", content=summary, tool_call_id=getattr(m, "tool_call_id", None), name=name))
            except Exception:
                out.append(m)
        else:
            out.append(m)
    return out
''',
    '''# 结果确认型工具：第一行就是精华（RESULT: path=... tx=... 之类），压成一行安全。
# 其余(默认)按"内容承载型"处理：read_file/str_replace/near_miss/diff 等，
# 精华常常不在第一行，压缩过头是长会话后期质量下滑的直接原因之一。
# NOTE: 这份名单是从代码里认出的工具名猜的，请对照 forge/tools/schemas.py
# 里的实际工具名核对一遍，缺漏的往"内容型"（默认分支）方向偏，不要往
# "确认型"偏——宁可少压缩，不要错压缩。
_CONFIRMATION_TOOLS = {
    "write_file", "modify_file", "undo_last_tx", "create_object",
    "delete_object", "freeze_object", "unlink_object", "link_objects",
    "run_test_structured", "apply_patch",
}


def _compress_messages(messages: list, keep_recent_tools: int = 6) -> list:
    """Replace older tool results with summaries to curb context rot.

    结果确认型工具压成一行；内容承载型工具保留更大预算（多行+字符上限），
    避免第20步之后模型"以为看过"实则早被压没了的内容。
    """
    if len(messages) < 24:
        return messages
    tool_idxs = [i for i, m in enumerate(messages) if getattr(m, "role", None) == "tool"]
    if len(tool_idxs) <= keep_recent_tools:
        return messages
    drop = set(tool_idxs[:-keep_recent_tools])
    out = []
    for i, m in enumerate(messages):
        if i in drop:
            name = getattr(m, "name", None) or "tool"
            content = (getattr(m, "content", None) or "")
            stripped = content.strip()
            if not stripped:
                summary = f"[compressed FACT {name}] "
            elif name in _CONFIRMATION_TOOLS:
                first = stripped.splitlines()[0][:120]
                summary = f"[compressed FACT {name}] {first}"
            else:
                lines = stripped.splitlines()
                kept = lines[:8]
                body = "\\n".join(kept)[:800]
                truncated = len(lines) > 8 or len(body) < len(stripped)
                more = (
                    f"\\n...[截断，原始长度 {len(stripped)} 字符/{len(lines)} 行]"
                    if truncated else ""
                )
                summary = f"[compressed {name}]\\n{body}{more}"
            try:
                from forge.adapters.base import Message as ForgeMessage
                out.append(ForgeMessage(role="tool", content=summary, tool_call_id=getattr(m, "tool_call_id", None), name=name))
            except Exception:
                out.append(m)
        else:
            out.append(m)
    return out
''',
    "runtime.py _compress_messages 按工具类型分层压缩",
)

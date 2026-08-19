"""Detect ambiguous tasks and build a one-shot clarification prompt."""
from __future__ import annotations

import re

_AMBIGUOUS = re.compile(
    r"(优化|重构|改进|弄好|改一下|整理一下|看看|polish|cleanup|improve|refactor|optimize)",
    re.I,
)
_HAS_ACCEPTANCE = re.compile(
    r"(测试|通过|性能|延迟|内存|删除|可读|命名|接口不变|行为不变|验收|"
    r"benchmark|pass|delete|readable|latency|coverage)",
    re.I,
)

# per-process: already clarified this session
_CLARIFIED = False


def reset() -> None:
    global _CLARIFIED
    _CLARIFIED = False


def mark_clarified() -> None:
    global _CLARIFIED
    _CLARIFIED = True


def is_clarified() -> bool:
    return _CLARIFIED


def needs_clarify(user_text: str) -> bool:
    if _CLARIFIED:
        return False
    t = (user_text or "").strip()
    if len(t) < 4:
        return False
    if not _AMBIGUOUS.search(t):
        return False
    if _HAS_ACCEPTANCE.search(t):
        return False
    return True


def clarification_message() -> str:
    return (
        "[system] 任务目标可能有歧义。请先用一句话确认验收标准，例如：\n"
        "- 性能：延迟/内存指标\n"
        "- 可读性：命名/结构，行为与测试保持不变\n"
        "- 删代码：可删范围，是否必须测试全绿\n"
        "确认前请勿做大范围 mutation。用户回复后即可继续。"
    )


def user_looks_like_clarification(user_text: str) -> bool:
    """If user replies with acceptance-ish content, mark clarified."""
    t = (user_text or "").strip()
    if _HAS_ACCEPTANCE.search(t) or len(t) < 80 and any(
        k in t for k in ("性能", "可读", "删除", "测试", "行为", "保持", "性能", "rename")
    ):
        return True
    return False

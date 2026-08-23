"""
计划确认词解析（生产路径 Runtime._handle_plan_reply 使用）。
用户确认指令解析"""
import re


def is_confirm(text: str) -> bool:
    t = text.strip().lower()
    if t in ("确认", "confirm", "yes", "y", "ok"):
        return True
    if re.match(r"^(确认|commit|confirm)\b", t):
        return True
    return False


def is_cancel(text: str) -> bool:
    t = text.strip().lower()
    if t in ("取消", "cancel", "no", "n", "abort"):
        return True
    if t.startswith("取消") or t.startswith("cancel") or t.startswith("abort"):
        return True
    return False

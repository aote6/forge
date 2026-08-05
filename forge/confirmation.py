"""用户确认指令解析"""
import re


def extract_confirmation(text: str) -> str | None:
    """
    支持格式：
      确认 abc123
      commit abc123
      确认 tx_abc123
    """
    patterns = [
        r"确认\s+([a-zA-Z0-9_-]+)",
        r"commit\s+([a-zA-Z0-9_-]+)",
    ]
    for p in patterns:
        m = re.search(p, text)
        if m:
            return m.group(1)
    return None

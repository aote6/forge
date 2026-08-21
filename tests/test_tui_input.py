"""tui_input 多行输入（bracketed paste）测试。

用注入的 key_source 模拟按键流，不依赖真实终端。
"""

import os

from forge import tui_input
from forge.tui_input import _read_key, read_multiline_input


class Feed:
    """把按键列表按顺序吐给读取器；耗尽后返回 None（EOF）。"""

    def __init__(self, keys):
        self._keys = list(keys)

    def __call__(self):
        return self._keys.pop(0) if self._keys else None


def feed(*chunks):
    """把字符串按字符拆开、转义序列保持整段，组成按键流。"""
    keys = []
    for c in chunks:
        if c.startswith("\x1b"):
            keys.append(c)
        else:
            keys.extend(c)
    return Feed(keys)


def test_enter_submits_single_line():
    assert read_multiline_input(key_source=feed("h", "i", "\r")) == "hi"


def test_empty_enter_returns_empty_string():
    assert read_multiline_input(key_source=feed("\r")) == ""


def test_bracketed_paste_is_one_message():
    out = read_multiline_input(
        key_source=feed("a", "\x1b[200~", "l1\nl2\nl3", "\x1b[201~", "b", "\r")
    )
    assert out == "al1\nl2\nl3b"


def test_paste_end_keeps_buffer_until_enter():
    # 粘贴结束不自动提交，回车后才提交
    out = read_multiline_input(key_source=feed("\x1b[200~", "x\ny", "\x1b[201~", "\r"))
    assert out == "x\ny"


def test_cr_inside_paste_normalized_to_newline():
    out = read_multiline_input(key_source=feed("\x1b[200~", "a\rb", "\x1b[201~"))
    assert out == "a\nb"


def test_backspace_deletes_last_char():
    assert read_multiline_input(key_source=feed("a", "b", "\x7f", "\r")) == "a"


def test_ctrl_c_raises_keyboard_interrupt():
    try:
        read_multiline_input(key_source=feed("a", "\x03"))
    except KeyboardInterrupt:
        return
    raise AssertionError("expected KeyboardInterrupt")


def test_ctrl_d_empty_returns_none():
    assert read_multiline_input(key_source=feed("\x04")) is None


def test_ctrl_d_with_buffer_submits_buffer():
    assert read_multiline_input(key_source=feed("ok", "\x04")) == "ok"


def test_eof_returns_none():
    assert read_multiline_input(key_source=Feed([])) is None


def test_backslash_continuation_joins_lines():
    out = read_multiline_input(key_source=feed("a", "\\", "\r", "b", "\r"))
    assert out == "a\nb"


def test_arrow_keys_ignored():
    out = read_multiline_input(
        key_source=feed("x", "\x1b[A", "\x1b[D", "y", "\r")
    )
    assert out == "xy"


def test_read_key_decodes_multibyte_utf8(monkeypatch):
    """逐字节喂入 UTF-8 中文「你」(E4 BD A0)，必须还原成一个字符，
    而不是 3 个 U+FFFD 替换符（显示为问号）。"""
    queue = list("你".encode("utf-8"))

    def fake_read(fd, n):
        return bytes([queue.pop(0)]) if queue else b""

    monkeypatch.setattr(tui_input.os, "read", fake_read)
    assert _read_key(0) == "你"


def test_read_key_multibyte_across_many_chars(monkeypatch):
    """连续多个中文 + ASCII 混合，逐字节流式读取仍完整还原。"""
    text = "你好，Forge！abc"
    queue = list(text.encode("utf-8"))

    def fake_read(fd, n):
        return bytes([queue.pop(0)]) if queue else b""

    monkeypatch.setattr(tui_input.os, "read", fake_read)
    out = []
    while True:
        ch = _read_key(0)
        if ch is None:
            break
        out.append(ch)
    assert "".join(out) == text

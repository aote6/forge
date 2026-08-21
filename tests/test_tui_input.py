"""tui_input 多行输入（bracketed paste）测试。

用注入的 key_source 模拟按键流，不依赖真实终端。
"""

import os
import unicodedata

from forge import tui_input
from forge.tui_input import _display_lines, _read_key, read_multiline_input


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


def _cols(ch):
    """终端列宽：组合符 0，东亚宽字符/emoji 2，其他 1（与实现一致）。"""
    if unicodedata.combining(ch):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in "WFA" else 1


class MiniVT:
    """极简 VT 模拟器：模拟自动折行、相对光标移动与清行，
    用来验证「重绘不会累积出重复行」。"""

    def __init__(self, width):
        self.width = width
        self.rows = [[" "] * width]
        self.cx = 0
        self.cy = 0

    def _grow(self, n):
        while len(self.rows) <= n:
            self.rows.append([" "] * self.width)

    def put(self, ch):
        w = _cols(ch) or 1
        self._grow(self.cy)
        if self.cx + w > self.width:  # 放不下 → 先折行再写
            self.cx = 0
            self.cy += 1
            self._grow(self.cy)
        self.rows[self.cy][self.cx] = ch
        for k in range(1, w):
            if self.cx + k < self.width:
                self.rows[self.cy][self.cx + k] = ""
        self.cx += w

    def _csi(self, params, final):
        n = 1
        if params:
            try:
                n = int(params.split(";")[0] or "1")
            except ValueError:
                n = 1
        if final == "A":  # 上移
            self.cy = max(0, self.cy - n)
        elif final == "B":  # 下移
            self.cy += n
            self._grow(self.cy)
        elif final == "K":  # 清行（\x1b[2K）
            self._grow(self.cy)
            self.rows[self.cy] = [" "] * self.width
        elif final == "J":  # 清到屏尾（兼容）
            self._grow(self.cy)
            for k in range(self.cx, self.width):
                self.rows[self.cy][k] = " "
            for cy in range(self.cy + 1, len(self.rows)):
                self.rows[cy] = [" "] * self.width

    def write(self, s):
        i = 0
        while i < len(s):
            c = s[i]
            if c == "\x1b":
                nxt = s[i + 1 : i + 2]
                if nxt == "[":
                    j = i + 2
                    while j < len(s) and not ("@" <= s[j] <= "~"):
                        j += 1
                    params = s[i + 2 : j]
                    final = s[j] if j < len(s) else ""
                    self._csi(params, final)
                    i = j + 1
                else:
                    i += 2
            elif c == "\r":
                self.cx = 0
                i += 1
            elif c == "\n":
                self.cx = 0
                self.cy += 1
                self._grow(self.cy)
                i += 1
            else:
                self.put(c)
                i += 1

    def screen(self):
        last = len(self.rows) - 1
        while last >= 0 and all(x in (" ", "") for x in self.rows[last]):
            last -= 1
        return ["".join(r).rstrip() for r in self.rows[: last + 1]]


def test_enter_submits_single_line():
    assert read_multiline_input(key_source=feed("h", "i", "\r")) == "hi"


def test_paste_multiline():
    out = read_multiline_input(
        key_source=feed("\x1b[200~", "l1\nl2\nl3", "\x1b[201~", "\r")
    )
    assert out == "l1\nl2\nl3"


def test_paste_with_prefix_suffix():
    out = read_multiline_input(
        key_source=feed("a", "\x1b[200~", "l1\nl2\nl3", "\x1b[201~", "b", "\r")
    )
    assert out == "al1\nl2\nl3b"


def test_paste_end_keeps_buffer_until_enter():
    out = read_multiline_input(
        key_source=feed("\x1b[200~", "x\ny", "\x1b[201~", "\r")
    )
    assert out == "x\ny"


def test_cr_inside_paste_normalized_to_newline():
    out = read_multiline_input(
        key_source=feed("\x1b[200~", "a\rb", "\x1b[201~")
    )
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
    """逐字节喂入 UTF-8 中文「你」(E4 BD A0)，必须还原成一个字符。"""
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


def test_long_wrapping_line_does_not_duplicate():
    """回归：打一行含中文的长字触发折行时，屏幕不应冒出重复行。"""
    vt = MiniVT(10)
    text = "你好世界，测试"  # 7 个宽字符 = 14 列，必然折行
    read_multiline_input(
        "> ", key_source=feed(text, "\r"), write=vt.write
    )
    full = "".join(vt.screen())
    assert full.count(text) == 1, f"重绘后屏幕出现重复：{vt.screen()!r}"


def test_display_lines_wide_chars():
    """宽字符折行计算正确。"""
    assert _display_lines("> 你好世界", 10) == 1
    assert _display_lines("> 你好世界！", 10) == 2
    assert _display_lines("a\nb\nc", 80) == 3


def test_narrow_termux_width_no_duplicate():
    """模拟 Termux COLUMNS=64 下连续输入中文，不应重复。"""
    vt = MiniVT(64)
    text = "这是一段在华为P20 Termux上测试的中文输入内容，用来验证折行重绘"
    read_multiline_input(
        "\n💬 > ", key_source=feed(text, "\r"), write=vt.write
    )
    full = "".join(vt.screen())
    assert full.count(text) == 1, f"出现重复：{vt.screen()!r}"

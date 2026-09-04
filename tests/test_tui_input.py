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

    def has_pending(self) -> bool:
        """True when more keys remain — models stdin still readable after CR."""
        return bool(self._keys)


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
    out = _run_input(vt, "> ", text, "\r")
    assert out == text
    # 精确等值：重绘后最终屏幕 == prompt + buffer，无重复、无残留、无截断
    assert "".join(vt.screen()) == "> " + text
    # 确认确实走了窄屏折行路径（宽度 10 下必然折行，光标落在内容末尾）
    assert (vt.cy, vt.cx) == _cursor_pos("> " + text, 10)


def test_display_lines_wide_chars():
    """宽字符折行计算正确。"""
    assert _display_lines("> 你好世界", 10) == 1
    assert _display_lines("> 你好世界！", 10) == 2
    assert _display_lines("a\nb\nc", 80) == 3


def test_narrow_termux_width_no_duplicate():
    """模拟 Termux COLUMNS=64 下连续输入中文，不应重复、不截断。"""
    vt = MiniVT(64)
    text = "这是一段在华为P20 Termux上测试的中文输入内容，用来验证折行重绘"
    out = _run_input(vt, "💬 > ", text, "\r")
    assert out == text
    # 精确等值：窄屏折行重绘后最终屏幕 == prompt + buffer，无重复/残留/截断
    assert "".join(vt.screen()) == "💬 > " + text
    # 确认确实走了 64 列折行路径（内容宽 > 64，光标落在内容末尾）
    assert (vt.cy, vt.cx) == _cursor_pos("💬 > " + text, 64)


def test_read_multiline_input_width_passthrough():
    """公开 API 的 width 透传：直接指定窄屏宽度，折行重绘无重复、不截断。"""
    vt = MiniVT(10)
    text = "你好世界，测试"  # 7 个宽字符 = 14 列 > 10，必然折行
    out = read_multiline_input(
        prompt="> ", key_source=feed(text, "\r"), write=vt.write, width=10
    )
    assert out == text
    assert "".join(vt.screen()) == "> " + text
    assert (vt.cy, vt.cx) == _cursor_pos("> " + text, 10)


def _cursor_pos(text, width):
    """按终端折行模型算出 text 末尾的光标位置 (行, 列)。

    与 _display_lines 同一套列宽/折行规则（宽字符 2 列、放不下先折行）。
    用作独立 oracle，校验 _render 之后 MiniVT 的光标确实落在内容末尾，
    而不是明显偏移。
    """
    col = 0
    row = 0
    for ch in text:
        if ch == "\n":
            row += 1
            col = 0
            continue
        w = _cols(ch) or 1
        if col + w > width:
            row += 1
            col = w
        else:
            col += w
    return row, col


def _run_input(vt, prompt, *chunks):
    """用 vt 的终端宽度驱动公开 API，返回提交结果。

    通过 read_multiline_input 的 width 透传注入 vt.width，保证 _render 的
    折行宽度与 MiniVT 模拟的终端宽度一致。
    """
    return read_multiline_input(
        prompt=prompt, key_source=feed(*chunks), write=vt.write, width=vt.width
    )


# ---- P2-4 回归测试：折行 / 重绘行为锁死（只补测试，不改实现） ----


def test_display_lines_emoji():
    """emoji 占 2 列的折行计算。"""
    assert _display_lines("😀", 2) == 1
    assert _display_lines("😀😀", 2) == 2
    assert _display_lines("😀😀😀", 4) == 2


def test_emoji_wrap_no_duplicate_no_truncation():
    """emoji 折行：不重复、不截断，整段只渲染一次。"""
    vt = MiniVT(10)
    text = "😀😀😀😀😀"  # 5 emoji × 2 = 10 列，加 "> " 必折行
    assert _run_input(vt, "> ", text, "\r") == text
    assert "".join(vt.screen()) == "> " + text


def test_emoji_cursor_position():
    """emoji 后光标列数按 2 列宽推进，不偏移。"""
    vt = MiniVT(10)
    _run_input(vt, "> ", "😀😀", "\r")
    assert (vt.cy, vt.cx) == (0, 6)  # "> " 2 列 + 2 emoji × 2 列


def test_paste_multiline_no_render_duplicate():
    """多行粘贴只整段渲染一次，屏幕上每行内容不重复、不截断。"""
    vt = MiniVT(40)
    out = _run_input(vt, "> ", "\x1b[200~", "line1\nline2\nline3", "\x1b[201~", "\r")
    assert out == "line1\nline2\nline3"
    assert "".join(vt.screen()) == "> line1line2line3"


def test_backspace_ascii_rewrites_exactly():
    """删除普通字符后整行重绘，无残留。"""
    vt = MiniVT(20)
    out = _run_input(vt, "> ", "abcdef", "\x7f", "\x7f", "\r")
    assert out == "abcd"
    assert "".join(vt.screen()) == "> abcd"


def test_backspace_deletes_wide_char():
    """删除中文宽字符后重绘正确，光标列数按剩余宽度收窄。"""
    vt = MiniVT(20)
    out = _run_input(vt, "> ", "你", "好", "\x7f", "\r")
    assert out == "你"
    assert "".join(vt.screen()) == "> 你"
    assert (vt.cy, vt.cx) == (0, 4)


def test_backspace_cross_wrap_clears_old_line():
    """跨折行删除：从 2 行退到 1 行，第 2 行旧内容被清掉。"""
    vt = MiniVT(10)
    text = "你好世界！"  # "> " + 5 宽字符 = 12 列 > 10 → 折成 2 行
    out = _run_input(vt, "> ", text, "\x7f", "\r")
    assert out == "你好世界"
    assert "".join(vt.screen()) == "> 你好世界"
    assert (vt.cy, vt.cx) == (0, 10)


def test_chinese_wrap_cursor_position():
    """中文折行后光标落在内容末尾，不重复不覆盖。"""
    vt = MiniVT(10)
    text = "你好世界！"
    _run_input(vt, "> ", text, "\r")
    assert "".join(vt.screen()) == "> " + text
    assert (vt.cy, vt.cx) == _cursor_pos("> " + text, 10)


def test_long_text_cursor_position_at_end():
    """长文本跨多行后光标最终位置正确，不产生重复行。"""
    vt = MiniVT(40)
    text = "这是一段用于验证长文本折行后光标最终位置的测试内容abc"
    _run_input(vt, "> ", text, "\r")
    assert "".join(vt.screen()) == "> " + text
    assert (vt.cy, vt.cx) == _cursor_pos("> " + text, 40)


def test_long_text_backspace_no_residue():
    """删除长文本末尾宽字符后不留残影，光标同步收窄。"""
    vt = MiniVT(40)
    text = "这是一段用来验证删除末尾宽字符后不会留下残影的长文本内容测试"
    out = _run_input(vt, "> ", text, "\x7f", "\r")
    expected = text[:-1]
    assert out == expected
    assert "".join(vt.screen()) == "> " + expected
    assert (vt.cy, vt.cx) == _cursor_pos("> " + expected, 40)


def test_narrow_backspace_no_residue():
    """窄终端（64 列）退格后不重复、不截断、不留残影。"""
    vt = MiniVT(64)
    text = "这是一段在华为P20 Termux上测试的中文输入内容，用来验证折行重绘"
    out = _run_input(vt, "💬 > ", text, "\x7f", "\x7f", "\x7f", "\r")
    expected = text[:-3]
    assert out == expected
    assert "".join(vt.screen()) == "💬 > " + expected


# ── bare multiline (no bracketed-paste markers) + pending lookahead ──


def test_bare_cr_with_pending_becomes_newline():
    """CR 到达时仍有后续键：CR → \\n，不提交。"""
    src = feed("a", "\r", "b", "\r")
    out = read_multiline_input(key_source=src, pending_check=src.has_pending)
    assert out == "a\nb"


def test_bare_multiline_paste_one_submission():
    """裸流 line1\\rline2\\rline3 + 最终 Enter → 一次得到三行。"""
    src = feed("line1", "\r", "line2", "\r", "line3", "\r")
    out = read_multiline_input(key_source=src, pending_check=src.has_pending)
    assert out == "line1\nline2\nline3"


def test_bare_multiline_waits_for_final_enter():
    """中间 CR 因 pending 不提交；队列耗尽后的 Enter 才提交。"""
    # After line3 there is one more \\r with empty pending → submit
    src = feed("x", "\r", "y", "\r")
    out = read_multiline_input(key_source=src, pending_check=src.has_pending)
    assert out == "x\ny"


def test_enter_without_pending_still_submits_immediately():
    """普通 hello\\r 且无 pending → 立即提交（不依赖真实时间）。"""
    src = feed("h", "e", "l", "l", "o", "\r")
    out = read_multiline_input(key_source=src, pending_check=src.has_pending)
    assert out == "hello"


def test_enter_without_pending_check_unchanged():
    """未注入 pending_check 时行为与改造前一致：首个 CR 即提交。"""
    out = read_multiline_input(key_source=feed("line1", "\r", "line2", "\r"))
    assert out == "line1"


def test_bare_lf_with_pending_becomes_newline():
    """LF 与 CR 同等：有 pending 时视为内容换行。"""
    src = feed("a", "\n", "b", "\n")
    out = read_multiline_input(key_source=src, pending_check=src.has_pending)
    assert out == "a\nb"


def test_backslash_continuation_still_works_with_pending_check():
    """反斜杠续行优先于 pending lookahead，行为不变。"""
    src = feed("a", "\\", "\r", "b", "\r")
    out = read_multiline_input(key_source=src, pending_check=src.has_pending)
    assert out == "a\nb"


def test_bracketed_paste_unaffected_by_pending_check():
    """BP 路径不走 pending 分支；注入 pending_check 也不应干扰。"""
    src = feed("\x1b[200~", "l1\nl2\nl3", "\x1b[201~", "\r")
    out = read_multiline_input(key_source=src, pending_check=src.has_pending)
    assert out == "l1\nl2\nl3"


def test_stdin_has_pending_uses_select(monkeypatch):
    """_stdin_has_pending 委托 select；可读 → True，超时 → False。"""
    calls = []

    def fake_select(r, w, x, timeout):
        calls.append(timeout)
        return (r if timeout == 0.0 else []), [], []

    monkeypatch.setattr(tui_input._select, "select", fake_select)
    assert tui_input._stdin_has_pending(0, timeout=0.0) is True
    assert tui_input._stdin_has_pending(0, timeout=0.015) is False
    assert calls == [0.0, 0.015]

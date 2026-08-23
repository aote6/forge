"""Terminal 多行输入（支持 bracketed paste）。

替代内置 input()，解决「从 app 复制多行内容粘贴时，
换行被当成发送、消息被截断」的问题。

行为：
- 交互时向终端发送 \\x1b[?2004h 启用 bracketed paste。
  Termux 等支持它的终端会把粘贴内容包在 \\x1b[200~ ... \\x1b[201~
  里，粘贴内部的换行是字面换行，只有 \\x1b[201~ 到达才整体提交。
- 手动兜底：行尾以反斜杠 \\ 结尾时，下一行继续拼接到同一条消息
  （适用于不支持 bracketed paste 的终端）。
- 基本编辑：退格删除；Ctrl+C 抛 KeyboardInterrupt；Ctrl+D 空输入返回 None。
- 非 tty / 非 POSIX / 测试注入 key_source 时回退为简单行为。

重绘策略（针对 Termux / 窄屏 / 软折行）：
- 不再使用 DECSC/DECRC（\\x1b7/\\x1b8）。在 Termux 上，当输入靠近屏幕底部、
  发生软折行或屏幕滚动后，绝对光标位置会失效，导致每次重绘从错误位置
  开始写，刷出「打一行字冒出十几行一模一样内容」。
- 改为基于实际显示宽度（east_asian_width）计算占用行数，用相对光标上移
  + 逐行清行（\\x1b[2K）后整段重写。宽字符/emoji/中文折行都能正确处理。

key_source / write 参数供测试注入，避免依赖真实终端。
"""

from __future__ import annotations

import os
import shutil
import sys
import unicodedata

try:
    import termios
    import tty

    _POSIX = True
except ImportError:  # pragma: no cover - 非 POSIX 平台
    termios = None
    tty = None
    _POSIX = False

_BP_START = "\x1b[200~"  # bracketed paste 开始
_BP_END = "\x1b[201~"  # bracketed paste 结束
_BP_ENABLE = "\x1b[?2004h"
_BP_DISABLE = "\x1b[?2004l"


def _char_width(ch: str) -> int:
    """终端列宽：组合符 0，东亚宽字符/emoji 2，其他 1。"""
    if not ch or unicodedata.combining(ch):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F", "A") else 1


def _display_lines(text: str, width: int) -> int:
    """计算 text 在给定终端宽度下会占多少物理行（含软折行）。"""
    if width < 2:
        width = 80
    lines = 1
    col = 0
    for ch in text:
        if ch == "\n":
            lines += 1
            col = 0
            continue
        w = _char_width(ch) or 1
        if col + w > width:
            lines += 1
            col = w
        else:
            col += w
    return max(1, lines)


def _get_terminal_width() -> int:
    try:
        return shutil.get_terminal_size(fallback=(80, 24)).columns
    except Exception:
        try:
            return int(os.environ.get("COLUMNS", "80")) or 80
        except Exception:
            return 80


def _split_leading_newlines(prompt: str) -> tuple[str, str]:
    """把 prompt 开头的 \\n 拆出来单独处理。"""
    i = 0
    while i < len(prompt) and prompt[i] == "\n":
        i += 1
    return prompt[:i], prompt[i:]


def _render(write, prompt: str, buffer: str, prev_lines: int, width: int) -> int:
    """整段重绘当前输入。返回新占用的物理行数。

    用相对上移 + 逐行 \\x1b[2K 清行，再重写。不依赖 DECSC/DECRC。
    """
    logical = prompt + buffer
    new_lines = _display_lines(logical, width)

    parts: list[str] = []
    # 回到上一轮内容的顶部
    if prev_lines > 1:
        parts.append(f"\x1b[{prev_lines - 1}A")
    parts.append("\r")

    # 清掉上一轮占用的所有行
    for i in range(prev_lines):
        parts.append("\x1b[2K")
        if i < prev_lines - 1:
            parts.append("\x1b[1B")
    # 清完后回到顶部
    if prev_lines > 1:
        parts.append(f"\x1b[{prev_lines - 1}A")
    parts.append("\r")

    # 重写 prompt + buffer（raw 模式下换行用 \r\n）
    parts.append(prompt)
    parts.append(buffer.replace("\n", "\r\n"))

    write("".join(parts))
    return new_lines


def _read_utf8_char(fd, b0: bytes) -> str:
    """把 UTF-8 首字节 b0 连同续字节读完整，解码为一个字符。

    中文等非 ASCII 字符是多字节序列（如「你」= E4 BD A0）。
    若逐字节单独 decode，每个字节都会变成 U+FFFD 替换符，
    终端上显示成问号/方框。因此必须先按首字节确定长度，
    把整段读齐再一次性 decode。
    """
    first = b0[0]
    if first & 0xF8 == 0xF0:
        need = 4
    elif first & 0xF0 == 0xE0:
        need = 3
    elif first & 0xE0 == 0xC0:
        need = 2
    else:
        need = 1
    seq = bytearray(b0)
    while len(seq) < need:
        b = os.read(fd, 1)
        if not b:
            break
        seq.append(b[0])
    return bytes(seq).decode("utf-8", "replace")


def _read_key(fd) -> str | None:
    """读一个按键/转义序列，返回 str；EOF 返回 None。"""
    b0 = os.read(fd, 1)
    if not b0:
        return None
    if b0 != b"\x1b":
        return _read_utf8_char(fd, b0)
    seq = bytearray(b0)
    b1 = os.read(fd, 1)
    if not b1:
        return "\x1b"
    seq += b1
    c1 = b1.decode("utf-8", "replace")
    if c1 == "[":
        # CSI 序列：读到终结字节（0x40-0x7E），如 \x1b[200~、\x1b[A
        while True:
            b = os.read(fd, 1)
            if not b:
                break
            seq += b
            if 0x40 <= b[0] <= 0x7E:
                break
    elif c1 == "]":
        # OSC 序列：读到 BEL 或 ST（\x1b\\）
        while True:
            b = os.read(fd, 1)
            if not b:
                break
            seq += b
            if b == b"\x07":
                break
            if len(seq) >= 2 and bytes(seq[-2:]) == b"\x1b\\":
                break
    return bytes(seq).decode("utf-8", "replace")


def _read_loop(
    prompt: str, key_source, write, width: int | None = None
) -> tuple[str | None, bool]:
    """核心状态机。返回 (result, submitted)；submitted=False 表示异常中断。"""
    if width is None:
        width = _get_terminal_width()

    leading, clean = _split_leading_newlines(prompt)
    # 先写出开头换行 + 提示符
    write(leading.replace("\n", "\r\n") + clean)
    buffer = ""
    in_paste = False
    prev_lines = _display_lines(clean, width)

    while True:
        ch = key_source()
        if ch is None:  # EOF
            return buffer or None, True
        if ch == _BP_START:
            in_paste = True
            continue
        if ch == _BP_END:
            # 粘贴结束：只退出粘贴模式，仍等待 Enter 提交
            in_paste = False
            prev_lines = _render(write, clean, buffer, prev_lines, width)
            continue
        if in_paste:
            # 粘贴内容：换行按字面处理，Ctrl+C/Ctrl+D 也当字面内容
            if ch == "\r":
                buffer += "\n"
            elif ch in ("\x7f", "\x08"):
                if buffer:
                    buffer = buffer[:-1]
            elif not ch.startswith("\x1b"):
                buffer += ch
            continue
        # 非粘贴：回车提交
        if ch in ("\r", "\n"):
            if buffer.rstrip().endswith("\\"):
                buffer = buffer.rstrip()[:-1] + "\n"
                prev_lines = _render(write, clean, buffer, prev_lines, width)
                continue
            return buffer, True
        if ch in ("\x7f", "\x08"):  # 退格
            if buffer:
                buffer = buffer[:-1]
                prev_lines = _render(write, clean, buffer, prev_lines, width)
            continue
        if ch == "\x03":
            raise KeyboardInterrupt
        if ch == "\x04":  # Ctrl+D
            return buffer or None, True
        if ch.startswith("\x1b"):
            continue  # 方向键等暂不支持，忽略
        if ch.isprintable() or ch == "\t":
            buffer += ch
            prev_lines = _render(write, clean, buffer, prev_lines, width)


def read_multiline_input(
    prompt: str = "> ", key_source=None, write=None, width: int | None = None
) -> str | None:
    """读取一条（可能多行）输入，返回 str；EOF / Ctrl+D 空输入返回 None。

    key_source / write 仅供测试注入；默认从终端读取。
    width 为 None 时用真实终端宽度（_get_terminal_width()），否则强制指定
    折行宽度（供窄屏测试与固定宽度场景使用）。纯透传，不改变算法。
    """
    if key_source is not None:
        result, _submitted = _read_loop(
            prompt, key_source, write or (lambda s: None), width=width
        )
        return result

    if not _POSIX:
        return input(prompt)  # pragma: no cover

    fd = sys.stdin.fileno()
    if not os.isatty(fd):
        return input(prompt)

    try:
        old = termios.tcgetattr(fd)
    except termios.error:
        return input(prompt)

    tty.setraw(fd)

    def _ks():
        return _read_key(fd)

    def _wr(s: str) -> None:
        sys.stdout.write(s)
        sys.stdout.flush()

    _wr(_BP_ENABLE)
    try:
        result, submitted = _read_loop(prompt, _ks, _wr, width=width)
        if submitted:
            if not (result or "").endswith("\n"):
                _wr("\r\n")  # 结束本行，让后续输出从新行开始
        return result
    except KeyboardInterrupt:
        _wr("\r\n")
        raise
    finally:
        try:
            _wr(_BP_DISABLE)
        except Exception as e:
            print(f"[tui_input] 关闭 bracketed-paste 失败: {e}", file=sys.stderr)
        termios.tcsetattr(fd, termios.TCSADRAIN, old)

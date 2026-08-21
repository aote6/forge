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

key_source / write 参数供测试注入，避免依赖真实终端。
"""

import os
import sys

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


def _split_leading_newlines(prompt: str) -> tuple[str, str]:
    """把 prompt 开头的 \\n 拆出来单独处理（避免干扰光标行数计算）。"""
    i = 0
    while i < len(prompt) and prompt[i] == "\n":
        i += 1
    return prompt[:i], prompt[i:]


def _render(write, prompt: str, buffer: str, prev_lines: int) -> int:
    """整段重绘当前输入。返回新占用的终端行数。"""
    new_lines = 1 + buffer.count("\n")
    parts = []
    up = prev_lines - 1
    if up > 0:
        parts.append(f"\x1b[{up}A")  # 光标上移回顶部
    parts.append("\r\x1b[2K")  # 行首 + 清当前行
    parts.append(prompt)
    parts.append(buffer.replace("\n", "\r\n"))  # raw mode 下手动回车
    parts.append("\x1b[J")  # 清到屏尾（处理内容变短的情况）
    write("".join(parts))
    return new_lines


def _read_key(fd) -> str | None:
    """读一个按键/转义序列，返回 str；EOF 返回 None。"""
    b0 = os.read(fd, 1)
    if not b0:
        return None
    if b0 != b"\x1b":
        return b0.decode("utf-8", "replace")
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


def _read_loop(prompt: str, key_source, write) -> tuple[str | None, bool]:
    """核心状态机。返回 (result, submitted)；submitted=False 表示异常中断。"""
    _leading, clean = _split_leading_newlines(prompt)
    buffer = ""
    in_paste = False
    prev_lines = 1
    while True:
        ch = key_source()
        if ch is None:  # EOF
            return buffer or None, True
        if ch == _BP_START:
            in_paste = True
            continue
        if ch == _BP_END:
            # 粘贴结束：只退出粘贴模式，仍等待 Enter 提交
            # （与 readline 一致，用户可在粘贴后继续补充内容）
            in_paste = False
            prev_lines = _render(write, clean, buffer, prev_lines)
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
                prev_lines = _render(write, clean, buffer, prev_lines)
                continue
            return buffer, True
        if ch in ("\x7f", "\x08"):  # 退格
            if buffer:
                buffer = buffer[:-1]
                prev_lines = _render(write, clean, buffer, prev_lines)
            continue
        if ch == "\x03":
            raise KeyboardInterrupt
        if ch == "\x04":  # Ctrl+D
            return buffer or None, True
        if ch.startswith("\x1b"):
            continue  # 方向键等暂不支持，忽略
        if ch.isprintable() or ch == "\t":
            buffer += ch
            prev_lines = _render(write, clean, buffer, prev_lines)


def read_multiline_input(prompt: str = "> ", key_source=None, write=None) -> str | None:
    """读取一条（可能多行）输入，返回 str；EOF / Ctrl+D 空输入返回 None。

    key_source / write 仅供测试注入；默认从终端读取。
    """
    if key_source is not None:
        result, _submitted = _read_loop(prompt, key_source, write or (lambda s: None))
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

    leading, clean = _split_leading_newlines(prompt)
    _wr(_BP_ENABLE)
    _wr(leading.replace("\n", "\r\n") + clean)
    try:
        result, submitted = _read_loop(prompt, _ks, _wr)
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
        except Exception:
            pass
        termios.tcsetattr(fd, termios.TCSADRAIN, old)

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

重绘策略：
- 写提示符前用 DECSC(\\x1b7) 记住「提示符行首」的绝对光标位置，
  每次重绘用 DECRC(\\x1b8) 跳回该位置再 \\x1b[J 清到屏尾后整段重写。
  这样不依赖「猜终端折行了几行」来算光标上移量，宽字符/emoji 折行、
  以及终端把光标停在行尾「待折行」状态都不会让光标错位——彻底避免
  之前「打一行字，结果冒出十多行一模一样的字」的问题。

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
_SAVE_CURSOR = "\x1b7"  # DECSC：保存光标位置（提示符行首）
_RESTORE_CURSOR = "\x1b8"  # DECRC：恢复光标位置


def _split_leading_newlines(prompt: str) -> tuple[str, str]:
    """把 prompt 开头的 \\n 拆出来单独处理（避免干扰光标行数计算）。"""
    i = 0
    while i < len(prompt) and prompt[i] == "\n":
        i += 1
    return prompt[:i], prompt[i:]


def _render(write, prompt: str, buffer: str) -> None:
    """整段重绘当前输入：跳回提示符行首、清到屏尾、再重写 prompt+buffer。"""
    write(_RESTORE_CURSOR + "\x1b[J" + prompt + buffer.replace("\n", "\r\n"))


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


def _read_loop(prompt: str, key_source, write) -> tuple[str | None, bool]:
    """核心状态机。返回 (result, submitted)；submitted=False 表示异常中断。"""
    leading, clean = _split_leading_newlines(prompt)
    # 先写出提示符，并在写提示符前用 DECSC 记住行首位置，
    # 之后每次重绘都用 DECRC 回跳到这里（绝对定位，不受折行影响）。
    write(leading.replace("\n", "\r\n") + _SAVE_CURSOR + clean)
    buffer = ""
    in_paste = False
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
            _render(write, clean, buffer)
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
                _render(write, clean, buffer)
                continue
            return buffer, True
        if ch in ("\x7f", "\x08"):  # 退格
            if buffer:
                buffer = buffer[:-1]
                _render(write, clean, buffer)
            continue
        if ch == "\x03":
            raise KeyboardInterrupt
        if ch == "\x04":  # Ctrl+D
            return buffer or None, True
        if ch.startswith("\x1b"):
            continue  # 方向键等暂不支持，忽略
        if ch.isprintable() or ch == "\t":
            buffer += ch
            _render(write, clean, buffer)


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

    _wr(_BP_ENABLE)
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

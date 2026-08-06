"""Lu Patch Adapter — 安全写入适配器

Forge 决定改什么，Lu 负责安全落盘。
Adapter 把 ChangeProposal 的操作翻译成 Lu 命令。
"""
import subprocess
import sys
import os

LU_PATCH = os.path.expanduser("~/lu/core/lu_patch.py")


def patch(
    path: str,
    old_text: str,
    new_text: str,
    start_line: int = None,
    end_line: int = None
) -> tuple[bool, str]:
    """安全写入文件。返回 (success, message)"""

    if not os.path.exists(LU_PATCH):
        return False, f"Lu patch engine 不存在: {LU_PATCH}"

    if not os.path.exists(path):
        return False, f"目标文件不存在: {path}"

    # 策略选择：优先用行号范围，其次用锚点，最后整文件
    cmd = ["python3", LU_PATCH]

    if start_line is not None and end_line is not None:
        if start_line == end_line:
            # 单行替换
            cmd += [path, "--line", str(start_line), "--text", new_text]
        else:
            # 范围替换
            cmd += [path, "--range", f"{start_line}:{end_line}", "--text", new_text]
    elif old_text:
        # 用锚点定位（old_text 的第一行作为锚点）
        first_line = old_text.split("\n")[0].strip()
        if first_line:
            # 计算 old_text 有多少行
            old_lines = old_text.count("\n") + (1 if old_text and not old_text.endswith("\n") else 0)
            if old_lines == 1:
                cmd += [path, "--anchor-line", first_line, "--text", new_text]
            else:
                # 多行用 anchor-before + anchor-after
                last_line = old_text.split("\n")[-1].strip() if old_text.strip() else first_line
                cmd += [path, "--anchor-before", first_line,
                        "--anchor-after", last_line, "--text", new_text]
    else:
        return False, "缺少定位信息（start_line 或 old_text）"

    print(f"  [Lu Adapter] {' '.join(cmd)}", file=sys.stderr)

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)

    if result.returncode == 0:
        return True, result.stdout.strip()
    else:
        return False, result.stderr.strip() or result.stdout.strip() or "Lu 写入失败"


def snapshot(path: str) -> tuple[bool, str]:
    """触发 Lu 快照（写入前自动快照，这里是手动触发）"""
    if not os.path.exists(LU_PATCH):
        return False, "Lu patch engine 不存在"

    cmd = ["python3", LU_PATCH, path, "--lock"]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    return result.returncode == 0, result.stdout.strip()


def rollback(path: str) -> tuple[bool, str]:
    """回滚到最新快照"""
    if not os.path.exists(LU_PATCH):
        return False, "Lu patch engine 不存在"

    cmd = ["python3", LU_PATCH, path, "--rollback"]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    return result.returncode == 0, result.stdout.strip()

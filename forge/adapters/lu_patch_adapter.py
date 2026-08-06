"""Lu Patch Adapter — 安全写入适配器

Forge 决定改什么，Lu 负责安全落盘。
"""
import subprocess
import sys
import os

LU_PATCH = os.path.expanduser("~/lu/core/lu_patch.py")


def _run_lu(cmd: list, timeout: int = 30) -> tuple[bool, str]:
    """运行 Lu 命令，返回 (success, message)"""
    if not os.path.exists(LU_PATCH):
        return False, f"Lu patch engine 不存在: {LU_PATCH}"

    print(f"  [Lu Adapter] {' '.join(cmd)}", file=sys.stderr)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)

    if result.returncode == 0:
        return True, result.stdout.strip()
    else:
        return False, result.stderr.strip() or result.stdout.strip() or "Lu 写入失败"


def patch(
    path: str,
    old_text: str,
    new_text: str,
    start_line: int = None,
    end_line: int = None
) -> tuple[bool, str]:
    """安全修改文件。返回 (success, message)"""

    if not os.path.exists(path):
        return False, f"目标文件不存在: {path}"

    cmd = ["python3", LU_PATCH]

    if start_line is not None and end_line is not None:
        if start_line == end_line:
            cmd += [path, "--line", str(start_line), "--text", new_text]
        else:
            cmd += [path, "--range", f"{start_line}:{end_line}", "--text", new_text]
    elif old_text:
        first_line = old_text.split("\n")[0].strip()
        if first_line:
            old_lines = old_text.count("\n") + (1 if old_text and not old_text.endswith("\n") else 0)
            if old_lines == 1:
                cmd += [path, "--anchor-line", first_line, "--text", new_text]
            else:
                last_line = old_text.split("\n")[-1].strip() if old_text.strip() else first_line
                cmd += [path, "--anchor-before", first_line,
                        "--anchor-after", last_line, "--text", new_text]
    else:
        return False, "缺少定位信息（start_line 或 old_text）"

    return _run_lu(cmd)


def create(path: str, content: str) -> tuple[bool, str]:
    """安全创建文件。写入临时文件，用 --whole-file 走原子写入"""
    import tempfile
    # 写临时文件
    with tempfile.NamedTemporaryFile(mode='w', suffix='.new', delete=False, encoding='utf-8') as f:
        f.write(content)
        tmp_path = f.name

    cmd = ["python3", LU_PATCH, path, "--whole-file", "--new-file", tmp_path]
    ok, msg = _run_lu(cmd)

    # 清理临时文件
    try:
        os.unlink(tmp_path)
    except Exception:
        pass

    return ok, msg


def delete(path: str) -> tuple[bool, str]:
    """安全删除文件。用 --whole-file + 空内容"""
    import tempfile
    with tempfile.NamedTemporaryFile(mode='w', suffix='.empty', delete=False, encoding='utf-8') as f:
        f.write("")
        tmp_path = f.name

    cmd = ["python3", LU_PATCH, path, "--whole-file", "--new-file", tmp_path]
    ok, msg = _run_lu(cmd)

    try:
        os.unlink(tmp_path)
    except Exception:
        pass

    # Lu 写入空文件成功后，手动删文件
    if ok and os.path.exists(path):
        try:
            os.remove(path)
            return True, f"已删除: {path}"
        except Exception as e:
            return False, str(e)

    return ok, msg


def snapshot(path: str) -> tuple[bool, str]:
    """手动触发快照"""
    cmd = ["python3", LU_PATCH, path, "--lock"]
    return _run_lu(cmd, timeout=10)


def rollback(path: str) -> tuple[bool, str]:
    """回滚到最新快照"""
    cmd = ["python3", LU_PATCH, path, "--rollback"]
    return _run_lu(cmd, timeout=10)

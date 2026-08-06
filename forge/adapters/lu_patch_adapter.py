"""Lu Patch Adapter — 安全写入适配器

Forge 决定改什么，Lu 负责安全落盘。
所有写入操作（create/modify/delete）统一走 Lu，保持唯一写入口。
"""
import subprocess
import sys
import os
import tempfile

LU_PATCH = os.path.expanduser("~/lu/core/lu_patch.py")


def _run_lu(cmd: list, timeout: int = 30) -> tuple[bool, str]:
    """运行 Lu 命令，返回 (success, message)"""
    if not os.path.exists(LU_PATCH):
        return False, f"Lu patch engine 不存在: {LU_PATCH}"

    print(f"  [Lu] {' '.join(cmd)}", file=sys.stderr)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)

    if result.returncode == 0:
        return True, result.stdout.strip()
    else:
        return False, result.stderr.strip() or result.stdout.strip() or "Lu 写入失败"


def _write_via_whole_file(path: str, content: str) -> tuple[bool, str]:
    """通过临时文件 + --whole-file 走 Lu 原子写入"""
    # 写临时文件
    tmp = tempfile.NamedTemporaryFile(mode='w', suffix='.new', delete=False, encoding='utf-8')
    tmp.write(content)
    tmp_path = tmp.name
    tmp.close()

    cmd = ["python3", LU_PATCH, path, "--whole-file", "--new-file", tmp_path]
    ok, msg = _run_lu(cmd)

    try:
        os.unlink(tmp_path)
    except Exception:
        pass

    return ok, msg


def patch(
    path: str,
    old_text: str,
    new_text: str,
    start_line: int = None,
    end_line: int = None
) -> tuple[bool, str]:
    """安全修改已有文件"""
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
        return False, "缺少定位信息"

    return _run_lu(cmd)


def create(path: str, content: str) -> tuple[bool, str]:
    """安全创建文件。先 touch + 再走 Lu whole-file 原子写入"""
    if os.path.exists(path):
        return False, f"文件已存在: {path}"

    # 创建目录
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    # touch 空文件，让 Lu 的 --whole-file 有 target 可替换
    with open(path, "w", encoding="utf-8") as f:
        f.write("")

    ok, msg = _write_via_whole_file(path, content)

    if not ok:
        # 写入失败，清理空文件
        try:
            os.remove(path)
        except Exception:
            pass

    return ok, msg


def delete(path: str) -> tuple[bool, str]:
    """安全删除文件。先通过 Lu 写空内容触发快照，再删除"""
    if not os.path.exists(path):
        return True, "文件不存在，无需删除"

    # Lu 写入空内容 → 触发快照 + 原子替换
    ok, msg = _write_via_whole_file(path, "")

    if ok:
        # Lu 成功写入空文件后，手动删除
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

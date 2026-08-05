"""工具函数"""
import subprocess
import json
import time
from pathlib import Path
from forge.adapters.base import ToolResult
from forge.core.security import is_dangerous_command, needs_git_confirmation, is_allowed_command

LOG_PATH = Path.home() / "forge" / ".forge" / "operation_log.jsonl"

def _log(name: str, args: dict, success: bool, note: str = ""):
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": time.time(), "tool": name, "args": args,
        "success": success, "note": note[:200]
    }
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

def make_tools(workspace, safe_mode="blacklist"):

    def read_file(path: str, start: int = 1, end: int = 0) -> ToolResult:
        try:
            content = workspace.read_file(path, start, end)
            _log("read_file", {"path": path}, True)
            return ToolResult.ok(display=content, payload={"path": path})
        except PermissionError as e:
            _log("read_file", {"path": path}, False, str(e))
            return ToolResult.fail(display=f"🚫 {e}")
        except FileNotFoundError:
            _log("read_file", {"path": path}, False, "not found")
            return ToolResult.fail(display=f"文件不存在: {path}")
        except Exception as e:
            _log("read_file", {"path": path}, False, str(e))
            return ToolResult.fail(display=f"读取失败: {e}")

    def prepare_write(path: str, operations: list) -> ToolResult:
        try:
            ok, msg, tx = workspace.prepare_write(path, operations)
        except PermissionError as e:
            _log("prepare_write", {"path": path}, False, str(e))
            return ToolResult.fail(display=f"🚫 {e}")
        if not ok:
            _log("prepare_write", {"path": path}, False, msg)
            return ToolResult.fail(display=msg)
        _log("prepare_write", {"path": path}, True, f"tx={tx.id}")
        display = (
            f"⏸️ 事务 {tx.id} 已准备，待确认。\n"
            f"📄 文件: {path}\n"
            f"📊 Diff:\n{tx.patch}\n"
            f"---\n💡 '确认 {tx.id}' 提交 | '取消 {tx.id}' 放弃"
        )
        return ToolResult.ok(display=display, payload={
            "transaction_id": tx.id, "path": path, "patch": tx.patch,
            "operations": tx.operations
        })

    def commit_write(transaction_id: str) -> ToolResult:
        ok, msg = workspace.commit_write(transaction_id)
        _log("commit_write", {"transaction_id": transaction_id}, ok, msg)
        return ToolResult.ok(display=msg) if ok else ToolResult.fail(display=msg)

    def cancel_write(transaction_id: str) -> ToolResult:
        ok, msg = workspace.cancel_write(transaction_id)
        _log("cancel_write", {"transaction_id": transaction_id}, ok, msg)
        return ToolResult.ok(display=msg) if ok else ToolResult.fail(display=msg)

    def search_code(pattern: str, path: str = ".") -> ToolResult:
        try:
            result = workspace.search_code(pattern, path)
            _log("search_code", {"pattern": pattern, "path": path}, True)
            return ToolResult.ok(display=result, payload={"pattern": pattern})
        except PermissionError as e:
            _log("search_code", {"pattern": pattern, "path": path}, False, str(e))
            return ToolResult.fail(display=f"🚫 {e}")

    def run_command(cmd: str, timeout: int = 60) -> ToolResult:
        # 1. 危险命令黑名单（任何模式都生效）
        blocked = is_dangerous_command(cmd)
        if blocked:
            _log("run_command", {"cmd": cmd}, False, f"blocked: {blocked}")
            return ToolResult.fail(
                display=f"🚫 命令被安全策略拦截（命中规则: {blocked}）\n如确需执行，请手动在终端运行。"
            )

        # 2. 白名单模式
        if safe_mode == "whitelist":
            if not is_allowed_command(cmd):
                _log("run_command", {"cmd": cmd}, False, "not in whitelist")
                return ToolResult.fail(
                    display=f"⏸️ 命令不在白名单中，需要确认:\n  {cmd}\n💡 请手动在终端执行或切换到黑名单模式。"
                )

        # 3. Git 命令确认
        git_check = needs_git_confirmation(cmd)
        if git_check:
            _log("run_command", {"cmd": cmd}, False, f"git confirm: {git_check}")
            return ToolResult.fail(
                display=f"⏸️ Git 操作需要确认:\n  {cmd}\n💡 请手动在终端执行此 Git 命令。"
            )

        # 4. 执行
        try:
            result = subprocess.run(
                cmd, shell=True, cwd=workspace.project_root,
                capture_output=True, text=True, timeout=timeout
            )
            display = f"$ {cmd}\n[exit {result.returncode}]\n"
            if result.stdout:
                display += f"--- stdout ---\n{result.stdout}\n"
            if result.stderr:
                display += f"--- stderr ---\n{result.stderr}\n"
            _log("run_command", {"cmd": cmd}, result.returncode == 0, f"exit={result.returncode}")
            payload = {"returncode": result.returncode, "cmd": cmd}
            if result.returncode == 0:
                return ToolResult.ok(display=display, payload=payload)
            return ToolResult.fail(display=display)
        except subprocess.TimeoutExpired:
            _log("run_command", {"cmd": cmd}, False, "timeout")
            return ToolResult.fail(display=f"命令超时（>{timeout}秒）: {cmd}")
        except Exception as e:
            _log("run_command", {"cmd": cmd}, False, str(e))
            return ToolResult.fail(display=f"执行失败: {e}")

    return {
        "read_file": read_file,
        "prepare_write": prepare_write,
        "commit_write": commit_write,
        "cancel_write": cancel_write,
        "search_code": search_code,
        "run_command": run_command,
    }

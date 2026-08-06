"""本地只读与命令工具（不涉及世界事务）。"""

from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

from forge.adapters.base import ToolResult
from forge.core.security import is_dangerous_command, needs_git_confirmation, is_allowed_command

LOG_PATH = Path.home() / "forge" / ".forge" / "operation_log.jsonl"
MAX_OUTPUT_CHARS = 8000


def _log(name: str, args: dict, success: bool, note: str = ""):
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "ts": time.time(), "tool": name, "args": args,
        "success": success, "note": note[:200],
    }
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _truncate(text: str) -> str:
    if len(text) > MAX_OUTPUT_CHARS:
        return text[:MAX_OUTPUT_CHARS] + "\n\n...[输出已截断]"
    return text


def make_local_tools(workspace, safe_mode: str = "blacklist") -> dict:
    def list_files(path: str = ".", depth: int = 2) -> ToolResult:
        try:
            root = Path(workspace.project_root) / path
            if not root.exists():
                return ToolResult.fail(display=f"目录不存在: {path}")
            result = []

            def walk(p, level):
                if level > depth:
                    return
                try:
                    for item in sorted(p.iterdir()):
                        if item.name.startswith('.') and item.name != '.':
                            continue
                        rel = str(item.relative_to(workspace.project_root))
                        prefix = "  " * level
                        if item.is_dir():
                            result.append(f"{prefix}📁 {rel}/")
                            walk(item, level + 1)
                        else:
                            result.append(f"{prefix}📄 {rel}")
                except PermissionError:
                    pass

            walk(root, 0)
            _log("list_files", {"path": path, "depth": depth}, True)
            return ToolResult.ok(
                display="\n".join(result),
                payload={"mutation": False},
            )
        except Exception as e:
            _log("list_files", {"path": path}, False, str(e))
            return ToolResult.fail(display=f"列出文件失败: {e}")

    def read_file(path: str, start: int = 1, end: int = 0) -> ToolResult:
        try:
            content = workspace.read_file(path, start, end)
            _log("read_file", {"path": path}, True)
            return ToolResult.ok(
                display=_truncate(content),
                payload={"path": path, "mutation": False},
            )
        except PermissionError as e:
            _log("read_file", {"path": path}, False, str(e))
            return ToolResult.fail(display=f"🚫 {e}")
        except FileNotFoundError:
            _log("read_file", {"path": path}, False, "not found")
            return ToolResult.fail(display=f"文件不存在: {path}")
        except Exception as e:
            _log("read_file", {"path": path}, False, str(e))
            return ToolResult.fail(display=f"读取失败: {e}")

    def search_code(pattern: str, path: str = ".") -> ToolResult:
        try:
            result = workspace.search_code(pattern, path)
            _log("search_code", {"pattern": pattern, "path": path}, True)
            return ToolResult.ok(
                display=_truncate(result),
                payload={"pattern": pattern, "mutation": False},
            )
        except PermissionError as e:
            _log("search_code", {"pattern": pattern, "path": path}, False, str(e))
            return ToolResult.fail(display=f"🚫 {e}")

    def git_diff() -> ToolResult:
        try:
            result = subprocess.run(
                "git diff", shell=True, cwd=workspace.project_root,
                capture_output=True, text=True, timeout=10,
            )
            output = result.stdout if result.stdout else "（无修改）"
            _log("git_diff", {}, True)
            return ToolResult.ok(
                display=_truncate(output),
                payload={"mutation": False, "phase": "verifying"},
            )
        except Exception as e:
            _log("git_diff", {}, False, str(e))
            return ToolResult.fail(display=f"git diff 失败: {e}")

    def run_command(cmd: str, timeout: int = 60) -> ToolResult:
        blocked = is_dangerous_command(cmd)
        if blocked:
            _log("run_command", {"cmd": cmd}, False, f"blocked: {blocked}")
            return ToolResult.fail(
                display=f"🚫 命令被安全策略拦截（命中规则: {blocked}）\n如确需执行，请手动在终端运行。"
            )

        if safe_mode == "whitelist":
            if not is_allowed_command(cmd):
                _log("run_command", {"cmd": cmd}, False, "not in whitelist")
                return ToolResult.fail(
                    display=f"⏸️ 命令不在白名单中，需要确认:\n  {cmd}\n💡 请手动在终端执行或切换到黑名单模式。"
                )

        git_check = needs_git_confirmation(cmd)
        if git_check:
            _log("run_command", {"cmd": cmd}, False, f"git confirm: {git_check}")
            return ToolResult.fail(
                display=f"⏸️ Git 操作需要确认:\n  {cmd}\n💡 请手动在终端执行此 Git 命令。"
            )

        try:
            result = subprocess.run(
                cmd, shell=True, cwd=workspace.project_root,
                capture_output=True, text=True, timeout=timeout,
            )
            display = f"$ {cmd}\n[exit {result.returncode}]\n"
            if result.stdout:
                display += f"--- stdout ---\n{result.stdout}\n"
            if result.stderr:
                display += f"--- stderr ---\n{result.stderr}\n"
            display = _truncate(display)
            _log("run_command", {"cmd": cmd}, result.returncode == 0, f"exit={result.returncode}")
            payload = {
                "returncode": result.returncode,
                "cmd": cmd,
                "mutation": False,
                "phase": "verifying",
            }
            if result.returncode == 0:
                return ToolResult.ok(display=display, payload=payload)
            return ToolResult.fail(display=display, payload=payload)
        except subprocess.TimeoutExpired:
            _log("run_command", {"cmd": cmd}, False, "timeout")
            return ToolResult.fail(display=f"命令超时（>{timeout}秒）: {cmd}")
        except Exception as e:
            _log("run_command", {"cmd": cmd}, False, str(e))
            return ToolResult.fail(display=f"执行失败: {e}")

    return {
        "list_files": list_files,
        "read_file": read_file,
        "search_code": search_code,
        "git_diff": git_diff,
        "run_command": run_command,
    }

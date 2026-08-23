"""Git 相关只读工具。"""

from __future__ import annotations

import subprocess

from forge.adapters.base import ToolResult
from forge.tools._common import _log, _truncate


def make_git_tools(workspace) -> dict:
    def get_diff_summary() -> ToolResult:
        """归纳 git diff 的语义变更，省 Token。"""
        try:
            res = subprocess.run(
                ["git", "diff", "--unified=3"],
                cwd=workspace.project_root, capture_output=True, text=True, check=False,
            )
            diff_text = res.stdout
            if not diff_text.strip():
                return ToolResult.ok(display="工作区干净，无变更。", payload={"mutation": False, "files_changed": []})

            files_changed = []
            added_functions = []
            removed_functions = []
            current_file = None
            for line in diff_text.splitlines():
                if line.startswith("+++ b/"):
                    current_file = line[6:]
                    files_changed.append(current_file)
                elif line.startswith("+def ") or line.startswith("+class "):
                    added_functions.append(f"{current_file}: {line[1:].strip()}")
                elif line.startswith("-def ") or line.startswith("-class "):
                    removed_functions.append(f"{current_file}: {line[1:].strip()}")

            _log("get_diff_summary", {}, True)
            output = f"变更文件 ({len(files_changed)}):\n"
            output += ("\n".join(f"  ~ {f}" for f in files_changed[:20]) if files_changed else "  (无)")
            if added_functions:
                output += f"\n\n新增符号 ({len(added_functions)}):\n" + "\n".join(f"  + {s}" for s in added_functions[:20])
            if removed_functions:
                output += f"\n\n删除符号 ({len(removed_functions)}):\n" + "\n".join(f"  - {s}" for s in removed_functions[:20])
            return ToolResult.ok(
                display=output,
                payload={"mutation": False, "files_changed": files_changed, "added": added_functions, "removed": removed_functions},
            )
        except Exception as e:
            _log("get_diff_summary", {}, False, str(e))
            return ToolResult.fail(display=f"get_diff_summary 失败: {e}")

    def git_status_enhanced() -> ToolResult:
        """查看完整 Git 状态：staged/unstaged/untracked、分支、最近提交。"""
        try:
            branch_res = subprocess.run(
                ["git", "branch", "--show-current"],
                cwd=workspace.project_root, capture_output=True, text=True, check=False,
            )
            branch = branch_res.stdout.strip() or "HEAD (detached)"

            log_res = subprocess.run(
                ["git", "log", "-1", "--oneline"],
                cwd=workspace.project_root, capture_output=True, text=True, check=False,
            )
            last_commit = log_res.stdout.strip() or "No commits yet"

            status_res = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=workspace.project_root, capture_output=True, text=True, check=False,
            )

            staged, unstaged, untracked = [], [], []
            for line in status_res.stdout.splitlines():
                if not line or len(line) < 3:
                    continue
                x, y, path = line[0], line[1], line[3:]
                if x in ("A", "M", "R", "C", "D"):
                    staged.append(path)
                if y in ("M", "D"):
                    unstaged.append(path)
                if x == "?" and y == "?":
                    untracked.append(path)

            output = (
                f"Branch: {branch}\n"
                f"Last Commit: {last_commit}\n\n"
                f"Staged ({len(staged)}):\n" + ("\n".join(f"  + {f}" for f in staged) if staged else "  (none)") + "\n\n"
                f"Unstaged ({len(unstaged)}):\n" + ("\n".join(f"  * {f}" for f in unstaged) if unstaged else "  (none)") + "\n\n"
                f"Untracked ({len(untracked)}):\n" + ("\n".join(f"  ? {f}" for f in untracked) if untracked else "  (none)")
            )
            _log("git_status_enhanced", {}, True)
            return ToolResult.ok(
                display=output,
                payload={"mutation": False, "branch": branch, "staged_count": len(staged), "unstaged_count": len(unstaged), "untracked_count": len(untracked)},
            )
        except Exception as e:
            _log("git_status_enhanced", {}, False, str(e))
            return ToolResult.fail(display=f"git_status_enhanced 失败: {e}")

    def read_git_version(path: str, revision: str = "HEAD~1") -> ToolResult:
        """读取文件在某个 git 版本的内容。"""
        try:
            cmd = ["git", "show", f"{revision}:{path}"]
            res = subprocess.run(cmd, cwd=workspace.project_root, capture_output=True, text=True, check=False)
            if res.returncode != 0:
                return ToolResult.fail(display=f"无法读取 {path} 在 {revision} 的版本: {res.stderr.strip()[:200]}")
            _log("read_git_version", {"path": path, "revision": revision}, True)
            return ToolResult.ok(
                display=_truncate(res.stdout),
                payload={"mutation": False, "path": path, "revision": revision},
            )
        except Exception as e:
            _log("read_git_version", {"path": path}, False, str(e))
            return ToolResult.fail(display=f"read_git_version 失败: {e}")

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

    def git_log(n: int = 10) -> ToolResult:
        try:
            result = subprocess.run(
                f"git log --oneline -{n}",
                shell=True, cwd=workspace.project_root,
                capture_output=True, text=True, timeout=10,
            )
            output = result.stdout if result.stdout else "（无提交历史）"
            _log("git_log", {"n": n}, True)
            return ToolResult.ok(
                display=_truncate(output),
                payload={"mutation": False},
            )
        except Exception as e:
            _log("git_log", {"n": n}, False, str(e))
            return ToolResult.fail(display=f"git log 失败: {e}")

    return {
        "git_diff": git_diff,
        "git_log": git_log,
        "git_status_enhanced": git_status_enhanced,
        "get_diff_summary": get_diff_summary,
        "read_git_version": read_git_version,
    }

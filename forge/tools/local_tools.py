"""本地只读与命令工具（不涉及世界事务）。"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
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


def make_local_tools(workspace, safe_mode: str = "blacklist", world_runtime=None) -> dict:
    def get_repo_map(root_dir: str = ".", max_tokens: int = 1500) -> ToolResult:
        """使用 Python 原生 ast 提取代码签名，压缩上下文。零额外依赖。"""
        try:
            root = Path(workspace.project_root) / root_dir
            if not root.exists():
                return ToolResult.fail(display=f"目录不存在: {root_dir}")
            summary_lines = []
            for dirpath, _, filenames in os.walk(root):
                if any(skip in dirpath for skip in (".git", "__pycache__", ".venv", ".pytest_cache")):
                    continue
                for f in filenames:
                    if not f.endswith(".py"):
                        continue
                    full_path = os.path.join(dirpath, f)
                    rel_path = os.path.relpath(full_path, workspace.project_root)
                    try:
                        with open(full_path, "r", encoding="utf-8") as file:
                            tree = ast.parse(file.read(), filename=rel_path)
                        file_signatures = []
                        for node in ast.walk(tree):
                            if isinstance(node, ast.ClassDef):
                                file_signatures.append(f"  class {node.name}:")
                            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                                args = [a.arg for a in node.args.args]
                                file_signatures.append(f"    def {node.name}({', '.join(args)}):")
                        if file_signatures:
                            summary_lines.append(f"File: {rel_path}")
                            summary_lines.extend(file_signatures)
                    except Exception:
                        continue
            result = "\n".join(summary_lines)
            if len(result) > max_tokens * 4:
                result = result[: max_tokens * 4] + "\n... [Repo Map Truncated]"
            _log("get_repo_map", {"root_dir": root_dir, "max_tokens": max_tokens}, True)
            return ToolResult.ok(
                display=result or "No Python signatures found.",
                payload={"mutation": False, "file_count": len(summary_lines)},
            )
        except Exception as e:
            _log("get_repo_map", {"root_dir": root_dir}, False, str(e))
            return ToolResult.fail(display=f"get_repo_map 失败: {e}")

    def read_files(requests: list) -> ToolResult:
        """批量读取多个文件的内容，支持行范围。"""
        try:
            if not isinstance(requests, list) or not requests:
                return ToolResult.fail(display="read_files 需要非空 requests 列表")
            outputs = []
            for req in requests:
                path = req.get("path")
                start_line = req.get("start_line")
                end_line = req.get("end_line")
                if not path:
                    outputs.append("--- (missing path) ---\nError: path is required")
                    continue
                try:
                    content = workspace.read_file(path, start_line or 1, end_line or 0)
                    header = f"--- {path}"
                    if start_line or end_line:
                        header += f" (lines {start_line or 1}-{end_line or 'end'})"
                    header += " ---"
                    outputs.append(header + "\n" + _truncate(content))
                except Exception as e:
                    outputs.append(f"--- {path} ---\nError: {e}")
            _log("read_files", {"count": len(requests)}, True)
            return ToolResult.ok(
                display="\n\n".join(outputs),
                payload={"mutation": False, "file_count": len(requests)},
            )
        except Exception as e:
            _log("read_files", {"count": len(requests) if isinstance(requests, list) else 0}, False, str(e))
            return ToolResult.fail(display=f"read_files 失败: {e}")

    def run_test_structured(target: str = "tests/") -> ToolResult:
        """运行 pytest 并返回结构化结果。"""
        try:
            cmd = f"python3 -m pytest {target} -q --tb=short"
            result = subprocess.run(
                cmd, shell=True, cwd=workspace.project_root,
                capture_output=True, text=True, timeout=120,
            )
            output = result.stdout + result.stderr
            failures = []
            for line in output.splitlines():
                if line.startswith("FAILED") or "AssertionError" in line or "Error" in line:
                    failures.append(line.strip()[:200])
            # 提取最后一行汇总
            summary = ""
            for line in output.splitlines():
                if "passed" in line or "failed" in line or "error" in line:
                    summary = line.strip()
                    break
            parsed = {
                "exit_code": result.returncode,
                "passed": result.returncode == 0,
                "summary": summary,
                "failed_tests": failures[:20],
            }
            _log("run_test_structured", {"target": target}, result.returncode == 0)
            display = _truncate(json.dumps(parsed, ensure_ascii=False, indent=2))
            if result.returncode == 0:
                return ToolResult.ok(display=display, payload={"mutation": False, **parsed})
            return ToolResult.fail(display=display, payload={"mutation": False, **parsed})
        except subprocess.TimeoutExpired:
            _log("run_test_structured", {"target": target}, False, "timeout")
            return ToolResult.fail(display=f"测试超时（>120秒）: {target}")
        except Exception as e:
            _log("run_test_structured", {"target": target}, False, str(e))
            return ToolResult.fail(display=f"run_test_structured 失败: {e}")

    def run_diagnostics(directory: str = ".") -> ToolResult:
        """运行 AST 语法检查，返回结构化诊断。"""
        try:
            root = Path(workspace.project_root) / directory
            if not root.exists():
                return ToolResult.fail(display=f"目录不存在: {directory}")
            errors = []
            checked = 0
            for dirpath, _, filenames in os.walk(root):
                if any(skip in dirpath for skip in (".git", "__pycache__", ".venv", ".pytest_cache")):
                    continue
                for f in filenames:
                    if not f.endswith(".py"):
                        continue
                    checked += 1
                    p = os.path.join(dirpath, f)
                    try:
                        with open(p, "r", encoding="utf-8") as file:
                            ast.parse(file.read(), filename=p)
                    except SyntaxError as se:
                        errors.append({
                            "file": os.path.relpath(p, workspace.project_root),
                            "line": se.lineno,
                            "error": f"SyntaxError: {se.msg}",
                        })
                    except Exception:
                        continue
            parsed = {
                "status": "clean" if not errors else "issues_found",
                "files_checked": checked,
                "error_count": len(errors),
                "issues": errors[:20],
            }
            _log("run_diagnostics", {"directory": directory}, not errors)
            display = _truncate(json.dumps(parsed, ensure_ascii=False, indent=2))
            if errors:
                return ToolResult.fail(display=display, payload={"mutation": False, **parsed})
            return ToolResult.ok(display=display, payload={"mutation": False, **parsed})
        except Exception as e:
            _log("run_diagnostics", {"directory": directory}, False, str(e))
            return ToolResult.fail(display=f"run_diagnostics 失败: {e}")

    def get_context_budget(tracked_files: list | None = None) -> ToolResult:
        """统计当前已跟踪文件的 Token 预算。"""
        try:
            total_chars = 0
            file_stats = []
            if tracked_files:
                for path in tracked_files:
                    full = Path(workspace.project_root) / path
                    if full.exists():
                        size = full.stat().st_size
                        total_chars += size
                        file_stats.append({"path": path, "est_tokens": size // 4})
            est_total = total_chars // 4
            parsed = {
                "total_estimated_tokens": est_total,
                "tracked_file_count": len(file_stats),
                "files": file_stats[:10],
                "status": "approaching_limit" if est_total > 80000 else "ok",
            }
            _log("get_context_budget", {"tracked": len(file_stats)}, True)
            return ToolResult.ok(
                display=json.dumps(parsed, ensure_ascii=False, indent=2),
                payload={"mutation": False, **parsed},
            )
        except Exception as e:
            _log("get_context_budget", {}, False, str(e))
            return ToolResult.fail(display=f"get_context_budget 失败: {e}")

    def inspect_last_intent(history_file: str = ".forge/last_intent.json") -> ToolResult:
        """查看上一次 Veritas 事务提交结果。"""
        try:
            full = Path(workspace.project_root) / history_file
            if not full.exists():
                return ToolResult.ok(
                    display=json.dumps({"status": "none", "message": f"No record at {history_file}"}, ensure_ascii=False, indent=2),
                    payload={"status": "none", "mutation": False},
                )
            with open(full, "r", encoding="utf-8") as f:
                data = json.load(f)
            _log("inspect_last_intent", {"history_file": history_file}, True)
            return ToolResult.ok(
                display=_truncate(json.dumps(data, ensure_ascii=False, indent=2)),
                payload={"mutation": False, **data},
            )
        except Exception as e:
            _log("inspect_last_intent", {"history_file": history_file}, False, str(e))
            return ToolResult.fail(display=f"inspect_last_intent 失败: {e}")

    def read_file_with_lines(path: str, start_line: int | None = None, end_line: int | None = None) -> ToolResult:
        """带显式行号读取文件，用于精准对齐 Planner 的 modify 参数。"""
        try:
            target = Path(workspace.project_root) / path
            if not target.exists():
                return ToolResult.fail(display=f"文件不存在: {path}")
            with open(target, "r", encoding="utf-8") as f:
                lines = f.readlines()
            total = len(lines)
            s_idx = (start_line - 1) if start_line and start_line > 0 else 0
            e_idx = end_line if end_line and end_line <= total else total
            formatted = []
            for idx in range(s_idx, e_idx):
                formatted.append(f"{idx + 1:4d} | {lines[idx].rstrip()}")
            _log("read_file_with_lines", {"path": path, "start": start_line, "end": end_line}, True)
            header = f"--- {path} (Total: {total} lines, Showing: {s_idx + 1}-{e_idx}) ---"
            return ToolResult.ok(
                display=header + "\n" + "\n".join(formatted),
                payload={"mutation": False, "total_lines": total},
            )
        except Exception as e:
            _log("read_file_with_lines", {"path": path}, False, str(e))
            return ToolResult.fail(display=f"read_file_with_lines 失败: {e}")

    def preview_line_mutation(path: str, start_line: int, end_line: int, new_text: str) -> ToolResult:
        """模拟行替换，预览替换后的上下文。只读，不修改文件。"""
        try:
            target = Path(workspace.project_root) / path
            if not target.exists():
                return ToolResult.fail(display=f"文件不存在: {path}")
            with open(target, "r", encoding="utf-8") as f:
                lines = f.readlines()
            total = len(lines)
            if not (1 <= start_line <= total) or not (1 <= end_line <= total) or start_line > end_line:
                return ToolResult.fail(
                    display=f"行号范围越界: start_line={start_line}, end_line={end_line}, 文件总行数={total}"
                )
            ctx_start = max(0, start_line - 4)
            ctx_end = min(total, end_line + 3)
            before = [f"{i+1:4d} | {lines[i].rstrip()}" for i in range(ctx_start, start_line - 1)]
            after = [f"{i+1:4d} | {lines[i].rstrip()}" for i in range(end_line, ctx_end)]
            new_formatted = [f"  + | {line}" for line in new_text.splitlines()]
            _log("preview_line_mutation", {"path": path, "start": start_line, "end": end_line}, True)
            output = (
                f"=== Preview Mutation: {path} ===\n"
                f"[原上下文 Line {ctx_start + 1}-{start_line - 1}]:\n" + ("\n".join(before) if before else "  (无)") + "\n\n"
                f"[替换 Lines {start_line}-{end_line}]:\n" + "\n".join(new_formatted) + "\n\n"
                f"[后续上下文 Line {end_line + 1}-{ctx_end}]:\n" + ("\n".join(after) if after else "  (无)")
            )
            return ToolResult.ok(display=output, payload={"mutation": False, "valid_range": True})
        except Exception as e:
            _log("preview_line_mutation", {"path": path}, False, str(e))
            return ToolResult.fail(display=f"preview_line_mutation 失败: {e}")

    def get_symbol_line_range(path: str, symbol_name: str) -> ToolResult:
        """查询文件内指定类或函数的精确起始/结束行号。"""
        try:
            target = Path(workspace.project_root) / path
            if not target.exists():
                return ToolResult.fail(display=f"文件不存在: {path}")
            with open(target, "r", encoding="utf-8") as f:
                content = f.read()
            tree = ast.parse(content, filename=path)
            target_node = None
            for node in ast.walk(tree):
                if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                    if node.name == symbol_name:
                        target_node = node
                        break
            if not target_node:
                return ToolResult.fail(display=f"在 {path} 中未找到符号 '{symbol_name}'")
            start_line = target_node.lineno
            end_line = getattr(target_node, "end_lineno", start_line)
            _log("get_symbol_line_range", {"path": path, "symbol": symbol_name}, True)
            display = (
                f"符号 '{symbol_name}' 在 {path} 中的位置:\n"
                f"  start_line: {start_line}\n"
                f"  end_line: {end_line}\n"
                f"  行数: {end_line - start_line + 1}"
            )
            return ToolResult.ok(
                display=display,
                payload={"mutation": False, "path": path, "symbol": symbol_name, "start_line": start_line, "end_line": end_line},
            )
        except Exception as e:
            _log("get_symbol_line_range", {"path": path}, False, str(e))
            return ToolResult.fail(display=f"get_symbol_line_range 失败: {e}")

    def find_symbol_definition(symbol_name: str) -> ToolResult:
        """在工程中精确查找类/函数/变量的定义位置。"""
        try:
            matches = []
            for root, _, files in os.walk(workspace.project_root):
                if any(skip in root for skip in (".git", "__pycache__", ".venv", ".pytest_cache")):
                    continue
                for f in files:
                    if not f.endswith(".py"):
                        continue
                    full_path = Path(root) / f
                    rel_path = str(full_path.relative_to(workspace.project_root))
                    try:
                        with open(full_path, "r", encoding="utf-8") as file:
                            tree = ast.parse(file.read(), filename=rel_path)
                        for node in ast.walk(tree):
                            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                                if node.name == symbol_name:
                                    kind = "class" if isinstance(node, ast.ClassDef) else "def"
                                    args = ""
                                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                                        args = ", ".join(a.arg for a in node.args.args)
                                    docstring = ast.get_docstring(node) or ""
                                    matches.append({
                                        "file": rel_path,
                                        "line": node.lineno,
                                        "kind": kind,
                                        "name": node.name,
                                        "args": args,
                                        "docstring": docstring[:100],
                                    })
                    except Exception:
                        continue
            _log("find_symbol_definition", {"symbol": symbol_name}, True)
            if not matches:
                return ToolResult.ok(
                    display=f"未找到符号 '{symbol_name}' 的定义。",
                    payload={"mutation": False, "matches": []},
                )
            lines = [f"找到 {len(matches)} 处定义:"]
            for m in matches:
                sig = m["name"] + ("(" + m["args"] + ")" if m["args"] else "()") if m["kind"] == "def" else m["name"]
                lines.append(f"  [{m['kind']}] {sig} -> {m['file']}:{m['line']}")
                if m["docstring"]:
                    lines.append(f"    doc: {m['docstring']}")
            return ToolResult.ok(display="\n".join(lines), payload={"mutation": False, "matches": matches})
        except Exception as e:
            _log("find_symbol_definition", {"symbol": symbol_name}, False, str(e))
            return ToolResult.fail(display=f"find_symbol_definition 失败: {e}")

    def get_call_chain(symbol_name: str) -> ToolResult:
        """查找某符号的所有直接调用者和被调用者。"""
        try:
            callers = set()
            callees = set()
            target_file = None
            target_line = None

            # 第一遍：找到符号定义位置
            for root, _, files in os.walk(workspace.project_root):
                if any(skip in root for skip in (".git", "__pycache__", ".venv", ".pytest_cache")):
                    continue
                for f in files:
                    if not f.endswith(".py"):
                        continue
                    full_path = Path(root) / f
                    rel_path = str(full_path.relative_to(workspace.project_root))
                    try:
                        with open(full_path, "r", encoding="utf-8") as file:
                            tree = ast.parse(file.read(), filename=rel_path)
                        for node in ast.walk(tree):
                            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == symbol_name:
                                target_file = rel_path
                                target_line = node.lineno
                                # 收集该函数内部调用
                                for sub in ast.walk(node):
                                    if isinstance(sub, ast.Call):
                                        if isinstance(sub.func, ast.Name):
                                            callees.add(sub.func.id)
                                        elif isinstance(sub.func, ast.Attribute):
                                            callees.add(sub.func.attr)
                    except Exception:
                        continue

            # 第二遍：找到所有调用该符号的位置
            for root, _, files in os.walk(workspace.project_root):
                if any(skip in root for skip in (".git", "__pycache__", ".venv", ".pytest_cache")):
                    continue
                for f in files:
                    if not f.endswith(".py"):
                        continue
                    full_path = Path(root) / f
                    rel_path = str(full_path.relative_to(workspace.project_root))
                    try:
                        with open(full_path, "r", encoding="utf-8") as file:
                            tree = ast.parse(file.read(), filename=rel_path)
                        for node in ast.walk(tree):
                            if isinstance(node, ast.Call):
                                if isinstance(node.func, ast.Name) and node.func.id == symbol_name:
                                    callers.add(f"{rel_path}:{node.lineno}")
                                elif isinstance(node.func, ast.Attribute) and node.func.attr == symbol_name:
                                    callers.add(f"{rel_path}:{node.lineno}")
                    except Exception:
                        continue

            _log("get_call_chain", {"symbol": symbol_name}, True)
            output = f"符号: {symbol_name}\n"
            if target_file:
                output += f"定义位置: {target_file}:{target_line}\n\n"
            output += f"调用者 ({len(callers)}):\n"
            output += ("\n".join(f"  ← {c}" for c in sorted(callers)) if callers else "  (无直接调用者)\n")
            output += f"\n被调用 ({len(callees)}):\n"
            output += ("\n".join(f"  → {c}" for c in sorted(callees)) if callees else "  (无直接调用)")
            return ToolResult.ok(
                display=output,
                payload={"mutation": False, "callers": sorted(callers), "callees": sorted(callees)},
            )
        except Exception as e:
            _log("get_call_chain", {"symbol": symbol_name}, False, str(e))
            return ToolResult.fail(display=f"get_call_chain 失败: {e}")

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

    def extract_code_skeleton(path: str) -> ToolResult:
        """提取 Python 文件代码骨架：保留类/函数签名和 import，隐藏函数体。"""
        try:
            target = Path(workspace.project_root) / path
            if not target.exists():
                return ToolResult.fail(display=f"文件不存在: {path}")
            with open(target, "r", encoding="utf-8") as f:
                lines = f.readlines()
            tree = ast.parse("".join(lines), filename=path)
            keep_lines = set()
            for node in ast.walk(tree):
                if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                    keep_lines.add(node.lineno)
                    for dec in node.decorator_list:
                        keep_lines.add(dec.lineno)
                elif isinstance(node, (ast.Import, ast.ImportFrom)):
                    keep_lines.add(node.lineno)
            skeleton = []
            for idx, line in enumerate(lines, 1):
                stripped = line.rstrip()
                if idx in keep_lines and not stripped.lstrip().startswith(("#", "\"", "\'", '"""')):
                    skeleton.append(f"{idx:4d} | {stripped}")
            _log("extract_code_skeleton", {"path": path}, True)
            output = f"--- {path} 骨架 ---\n" + "\n".join(skeleton) if skeleton else f"--- {path} 骨架 ---\n(无法解析)"
            return ToolResult.ok(display=output, payload={"mutation": False, "line_count": len(skeleton)})
        except Exception as e:
            _log("extract_code_skeleton", {"path": path}, False, str(e))
            return ToolResult.fail(display=f"extract_code_skeleton 失败: {e}")

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

    def list_tests(directory: str = ".") -> ToolResult:
        """列出项目中所有测试文件。"""
        try:
            search_dir = Path(workspace.project_root) / directory
            if not search_dir.exists():
                return ToolResult.fail(display=f"目录不存在: {directory}")
            test_files = []
            for root, _, files in os.walk(search_dir):
                if any(skip in root for skip in (".git", "__pycache__", ".venv", ".pytest_cache")):
                    continue
                for f in files:
                    if f.endswith(".py") and (f.startswith("test_") or f.endswith("_test.py")):
                        full = Path(root) / f
                        rel = full.relative_to(workspace.project_root)
                        test_files.append(str(rel))
            test_files.sort()
            _log("list_tests", {"directory": directory}, True)
            if test_files:
                display = f"找到 {len(test_files)} 个测试文件:\n" + "\n".join(f"  {f}" for f in test_files)
            else:
                display = "未找到任何测试文件。"
            return ToolResult.ok(display=display, payload={"mutation": False, "tests": test_files})
        except Exception as e:
            _log("list_tests", {"directory": directory}, False, str(e))
            return ToolResult.fail(display=f"list_tests 失败: {e}")

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

    def search_history(query: str, max_results: int = 5) -> ToolResult:
        """搜索对话历史日志中的关键信息。"""
        try:
            log_file = Path(workspace.project_root) / ".forge" / "conversation_log.jsonl"
            if not log_file.exists():
                return ToolResult.ok(
                    display="尚无历史对话日志。",
                    payload={"mutation": False, "matches": []},
                )
            pattern = re.compile(query, re.IGNORECASE)
            matches = []
            with open(log_file, "r", encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        record = json.loads(line)
                        content = record.get("content", "")
                        if pattern.search(content):
                            matches.append(record)
                    except json.JSONDecodeError:
                        continue
            matches = matches[-max_results:]
            _log("search_history", {"query": query}, True)
            if not matches:
                return ToolResult.ok(
                    display=f"未找到匹配 '{query}' 的历史记录。",
                    payload={"mutation": False, "matches": []},
                )
            lines = [f"匹配到 {len(matches)} 条:"]
            for i, m in enumerate(matches, 1):
                role = m.get("role", "unknown")
                text = m.get("content", "").strip()
                snippet = text[:150] + ("..." if len(text) > 150 else "")
                lines.append(f"[{i}] {role}: {snippet}")
            return ToolResult.ok(display="\n".join(lines), payload={"mutation": False, "matches": matches})
        except Exception as e:
            _log("search_history", {"query": query}, False, str(e))
            return ToolResult.fail(display=f"search_history 失败: {e}")

    def summarize_file(path: str) -> ToolResult:
        """生成或读取文件的 AST 摘要，带 .forge 缓存。"""
        try:
            target = Path(workspace.project_root) / path
            if not target.exists():
                return ToolResult.fail(display=f"文件不存在: {path}")
            cache_dir = Path(workspace.project_root) / ".forge" / "summaries"
            cache_dir.mkdir(parents=True, exist_ok=True)
            path_hash = hashlib.sha256(str(path).encode("utf-8")).hexdigest()
            cache_file = cache_dir / f"{path_hash}.json"
            mtime = target.stat().st_mtime

            if cache_file.exists():
                try:
                    with open(cache_file, "r", encoding="utf-8") as f:
                        cached = json.load(f)
                    if cached.get("mtime") == mtime:
                        _log("summarize_file", {"path": path, "cached": True}, True)
                        display = f"[缓存] {path} 摘要:\n" + json.dumps(cached["summary"], ensure_ascii=False, indent=2)
                        return ToolResult.ok(display=display, payload={"mutation": False, "cached": True, "summary": cached["summary"]})
                except Exception:
                    pass

            with open(target, "r", encoding="utf-8") as f:
                code = f.read()
            tree = ast.parse(code, filename=path)
            imports, classes, functions = [], [], []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        imports.append(alias.name)
                elif isinstance(node, ast.ImportFrom):
                    module = node.module or ""
                    for alias in node.names:
                        imports.append(f"{module}.{alias.name}")
                elif isinstance(node, ast.ClassDef):
                    classes.append(node.name)
                elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    args = [a.arg for a in node.args.args]
                    functions.append(f"{node.name}({', '.join(args)})")
            summary = {"imports": imports, "classes": classes, "functions": functions}
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump({"mtime": mtime, "summary": summary}, f, ensure_ascii=False, indent=2)
            _log("summarize_file", {"path": path, "cached": False}, True)
            display = f"{path} 摘要:\n" + json.dumps(summary, ensure_ascii=False, indent=2)
            return ToolResult.ok(display=display, payload={"mutation": False, "cached": False, "summary": summary})
        except Exception as e:
            _log("summarize_file", {"path": path}, False, str(e))
            return ToolResult.fail(display=f"summarize_file 失败: {e}")

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

    def world_info() -> ToolResult:
        """查看 Veritas 世界摘要：版本、root hash、对象数。"""
        if world_runtime is None:
            return ToolResult.fail(display="World runtime 不可用（veritasd 未启动或未配置）")
        try:
            info = world_runtime.world_info()
            _log("world_info", {}, True)
            return ToolResult.ok(
                display=(
                    f"World version: {info.version}\n"
                    f"State root: {info.state_root}\n"
                    f"Object count: {info.object_count}"
                ),
                payload={"version": info.version, "object_count": info.object_count, "mutation": False},
            )
        except Exception as e:
            _log("world_info", {}, False, str(e))
            return ToolResult.fail(display=f"world_info 失败: {e}")

    def list_world_objects() -> ToolResult:
        """查看 Veritas 世界中的所有对象。"""
        if world_runtime is None:
            return ToolResult.fail(display="World runtime 不可用")
        try:
            objects = world_runtime.list_objects()
            if not objects:
                return ToolResult.ok(display="（世界中暂无对象）", payload={"objects": [], "mutation": False})
            lines = [f"  id={obj.object_id} state={obj.state}" for obj in objects]
            _log("list_world_objects", {}, True)
            return ToolResult.ok(
                display="\n".join(lines),
                payload={"objects": [{"id": o.object_id, "state": o.state} for o in objects], "mutation": False},
            )
        except Exception as e:
            _log("list_world_objects", {}, False, str(e))
            return ToolResult.fail(display=f"list_world_objects 失败: {e}")

    def get_world_object(object_id: int) -> ToolResult:
        """查看指定对象的状态。"""
        if world_runtime is None:
            return ToolResult.fail(display="World runtime 不可用")
        try:
            obj = world_runtime.get_object(object_id)
            if obj is None:
                return ToolResult.fail(display=f"对象 {object_id} 不存在")
            _log("get_world_object", {"object_id": object_id}, True)
            return ToolResult.ok(
                display=f"id={obj.object_id} state={obj.state}",
                payload={"id": obj.object_id, "state": obj.state, "mutation": False},
            )
        except Exception as e:
            _log("get_world_object", {"object_id": object_id}, False, str(e))
            return ToolResult.fail(display=f"get_world_object 失败: {e}")

    def list_world_links() -> ToolResult:
        """查看 Veritas 世界中的所有对象链接。"""
        if world_runtime is None:
            return ToolResult.fail(display="World runtime 不可用")
        try:
            links = world_runtime.get_links()
            if not links:
                return ToolResult.ok(display="（世界中暂无链接）", payload={"links": [], "mutation": False})
            lines = [f"  {l.from_id} -[{l.link_type}]-> {l.to_id}" for l in links]
            _log("list_world_links", {}, True)
            return ToolResult.ok(
                display="\n".join(lines),
                payload={"links": [{"from": l.from_id, "to": l.to_id, "type": l.link_type} for l in links], "mutation": False},
            )
        except Exception as e:
            _log("list_world_links", {}, False, str(e))
            return ToolResult.fail(display=f"list_world_links 失败: {e}")

    def run_single_test(path: str, timeout: int = 60) -> ToolResult:
        """Run a single pytest file or test node."""
        try:
            cmd = f"python3 -m pytest {path} -v --tb=short"
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
            _log("run_single_test", {"path": path}, result.returncode == 0, f"exit={result.returncode}")
            if result.returncode == 0:
                return ToolResult.ok(display=display, payload={"mutation": False, "phase": "verifying"})
            return ToolResult.fail(display=display, payload={"mutation": False, "phase": "verifying"})
        except Exception as e:
            _log("run_single_test", {"path": path}, False, str(e))
            return ToolResult.fail(display=f"run_single_test 失败: {e}")

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
        "read_file_with_lines": read_file_with_lines,
        "preview_line_mutation": preview_line_mutation,
        "get_symbol_line_range": get_symbol_line_range,
        "find_symbol_definition": find_symbol_definition,
        "get_call_chain": get_call_chain,
        "get_diff_summary": get_diff_summary,
        "extract_code_skeleton": extract_code_skeleton,
        "git_status_enhanced": git_status_enhanced,
        "list_tests": list_tests,
        "read_git_version": read_git_version,
        "search_history": search_history,
        "summarize_file": summarize_file,
        "get_repo_map": get_repo_map,
        "read_files": read_files,
        "run_test_structured": run_test_structured,
        "run_diagnostics": run_diagnostics,
        "get_context_budget": get_context_budget,
        "inspect_last_intent": inspect_last_intent,
        "list_files": list_files,
        "read_file": read_file,
        "search_code": search_code,
        "git_diff": git_diff,
        "git_log": git_log,
        "run_single_test": run_single_test,
        "run_command": run_command,
        "world_info": world_info,
        "list_world_objects": list_world_objects,
        "get_world_object": get_world_object,
        "list_world_links": list_world_links,
    }

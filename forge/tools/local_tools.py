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
from forge.core.security import is_dangerous_command, needs_git_confirmation, is_allowed_command, is_blocked_path, resolve_workspace_path, PathSecurityError
from forge.tools.display import format_block, error_slices
from forge.tools.project_memory import load_memory, update_memory, format_for_prompt
from forge.tools.read_cache import get as cache_get, put as cache_put
from forge.tools.errors import decorate_fail_message, classify_error
from forge.tools.session_changes import list_changes, format_list as format_session_changes, clear as clear_session_changes

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
    """Default: keep the tail (errors usually at the end)."""
    if len(text) > MAX_OUTPUT_CHARS:
        return "...[输出已截断前部]\n\n" + text[-MAX_OUTPUT_CHARS:]
    return text


def _truncate_head(text: str) -> str:
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
                    no_range = not start_line and not end_line
                    cached = cache_get(workspace.project_root, path) if no_range else None
                    if cached:
                        content = cached[0]
                    else:
                        content = workspace.read_file(path, start_line or 1, end_line or 0)
                        if no_range:
                            try:
                                cache_put(workspace.project_root, path, content)
                            except Exception:
                                pass
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
        """跑 pytest，失败时附带失败行前后源码上下文。"""
        import subprocess
        try:
            cmd = ["python3", "-m", "pytest", target, "-q", "--tb=short"]
            r = subprocess.run(
                cmd, cwd=workspace.project_root, capture_output=True, text=True, timeout=120
            )
            out = (r.stdout or "") + (r.stderr or "")
            failures = []
            for line in out.splitlines():
                if " FAILED" in line or line.startswith("FAILED "):
                    failures.append(line.strip()[:200])
            # Extract file:line from short traceback and attach source windows
            contexts = []
            import re as _re
            for mline in out.splitlines():
                mm = _re.search(r'([\w./\\-]+\.py):(\d+):', mline)
                if not mm:
                    continue
                rel, ln = mm.group(1), int(mm.group(2))
                full = Path(workspace.project_root) / rel
                if not full.is_file():
                    continue
                try:
                    lines = full.read_text(encoding="utf-8", errors="replace").splitlines()
                except OSError:
                    continue
                i = max(0, ln - 1)
                lo, hi = max(0, i - 5), min(len(lines), i + 6)
                snippet = []
                for j in range(lo, hi):
                    mark = ">>" if j == i else "  "
                    snippet.append(f"{mark} {j+1}: {lines[j]}")
                contexts.append({"file": rel, "line": ln, "snippet": "\n".join(snippet)})
            # dedupe by file:line
            seen = set()
            uniq = []
            for c in contexts:
                k = (c["file"], c["line"])
                if k in seen:
                    continue
                seen.add(k)
                uniq.append(c)
            prefix = "RESULT" if r.returncode == 0 else "FAILED"
            display = f"{prefix}: pytest target={target} exit={r.returncode}\n{_truncate(out)}"
            if uniq:
                display += "\n\n--- failure context ---"
                for c in uniq[:8]:
                    display += f"\n{c['file']}:{c['line']}\n{c['snippet']}\n"
            _log("run_test_structured", {"target": target}, r.returncode == 0)
            failure_context = [
                {"file": c["file"], "line": c["line"], "source": c["snippet"]}
                for c in uniq[:8]
            ]
            payload = {
                "returncode": r.returncode,
                "failed_tests": failures,
                "failures": failures,
                "contexts": uniq[:8],
                "failure_context": failure_context,
                "mutation": False,
                "phase": "verifying",
            }
            if r.returncode == 0:
                return ToolResult.ok(display=display, payload=payload)
            return ToolResult.fail(
                display=display + "\n建议: 根据 failure_context 源码窗口直接修复，无需再 read_file。",
                payload=payload,
            )
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


    def run_type_check(path: str = ".", tool: str = "auto") -> ToolResult:
        """类型检查：优先 mypy/pyright，否则做基础 AST 注解一致性启发式。"""
        import shutil
        import subprocess
        root = Path(workspace.project_root)

        def _run(cmd):
            return subprocess.run(cmd, cwd=workspace.project_root, capture_output=True, text=True, timeout=90)

        chosen = tool
        if tool == "auto":
            if shutil.which("mypy"):
                chosen = "mypy"
            elif shutil.which("pyright"):
                chosen = "pyright"
            else:
                chosen = "ast"

        if chosen == "mypy":
            r = _run(["mypy", "--ignore-missing-imports", "--no-error-summary", path if path != "." else "."])
            display = f"$ mypy {path}\n[exit {r.returncode}]\n{_truncate((r.stdout or '') + (r.stderr or ''))}"
            ok = r.returncode == 0
            _log("run_type_check", {"tool": "mypy"}, ok)
            return (ToolResult.ok if ok else ToolResult.fail)(display=display, payload={"tool": "mypy", "returncode": r.returncode})

        if chosen == "pyright":
            r = _run(["pyright", "--outputjson", path if path != "." else "."])
            display = f"$ pyright {path}\n[exit {r.returncode}]\n{_truncate((r.stdout or '') + (r.stderr or ''))}"
            ok = r.returncode == 0
            _log("run_type_check", {"tool": "pyright"}, ok)
            return (ToolResult.ok if ok else ToolResult.fail)(display=display, payload={"tool": "pyright", "returncode": r.returncode})

        # AST heuristic: compare annotated assigns with obvious wrong literal types
        issues = []
        import ast as _ast
        skip = {".git", ".forge", "__pycache__", ".venv", "venv"}
        files = []
        base = root / path if path != "." else root
        if base.is_file() and str(base).endswith(".py"):
            files = [base]
        else:
            for dp, dns, fns in os.walk(base):
                dns[:] = [d for d in dns if d not in skip]
                for fn in fns:
                    if fn.endswith(".py"):
                        files.append(Path(dp) / fn)
        for fp in files[:80]:
            try:
                tree = _ast.parse(fp.read_text(encoding="utf-8", errors="replace"))
            except (SyntaxError, OSError) as e:
                issues.append(f"{fp.relative_to(root)}: parse error: {e}")
                continue
            for node in _ast.walk(tree):
                if isinstance(node, _ast.AnnAssign) and node.value is not None and node.annotation is not None:
                    ann = node.annotation
                    ann_name = ann.id if isinstance(ann, _ast.Name) else None
                    val = node.value
                    if ann_name == "int" and isinstance(val, _ast.Constant) and not isinstance(val.value, int):
                        issues.append(f"{fp.relative_to(root)}:{node.lineno}: annotated int got {type(val.value).__name__}")
                    if ann_name == "str" and isinstance(val, _ast.Constant) and not isinstance(val.value, str):
                        issues.append(f"{fp.relative_to(root)}:{node.lineno}: annotated str got {type(val.value).__name__}")
                    if ann_name == "bool" and isinstance(val, _ast.Constant) and not isinstance(val.value, bool):
                        issues.append(f"{fp.relative_to(root)}:{node.lineno}: annotated bool got {type(val.value).__name__}")
        if not issues:
            display = "run_type_check(ast): no obvious annotation mismatches (mypy/pyright not installed)"
            _log("run_type_check", {"tool": "ast"}, True)
            return ToolResult.ok(display=display, payload={"tool": "ast", "issues": []})
        display = "run_type_check(ast) issues:\n" + "\n".join(issues[:40])
        _log("run_type_check", {"tool": "ast"}, False)
        return ToolResult.fail(display=display, payload={"tool": "ast", "issues": issues[:40]})


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
        """全仓符号索引查找定义（.forge/symbols.json），避免逐文件 AST 全扫。"""
        try:
            from forge.core.symbol_index import lookup_symbol, load_symbol_index
            load_symbol_index(workspace.project_root)  # ensure cache
            hits = lookup_symbol(workspace.project_root, symbol_name, exact=True)
            if not hits:
                # soft match
                hits = lookup_symbol(workspace.project_root, symbol_name, exact=False)[:20]
                if not hits:
                    _log("find_symbol_definition", {"symbol": symbol_name}, True, "not found")
                    return ToolResult.ok(
                        display=(
                            f"未找到符号 '{symbol_name}'\n"
                            f"建议: 检查拼写，或用 search_code 做文本搜索。"
                        ),
                        payload={"symbol": symbol_name, "hits": [], "matches": []},
                    )
            lines = [f"符号 '{symbol_name}' 命中 {len(hits)} 处:"]
            for h in hits[:30]:
                lines.append(
                    f"  {h.get('kind','?')} {h.get('qualname', symbol_name)} "
                    f"@ {h['path']}:{h['start_line']}-{h['end_line']}"
                )
            display = "\n".join(lines)
            _log("find_symbol_definition", {"symbol": symbol_name, "hits": len(hits)}, True)
            return ToolResult.ok(
                display=display,
                payload={"symbol": symbol_name, "hits": hits[:30]},
            )
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
        """读取文件。大文件无行范围时返回符号大纲，避免撑爆上下文。"""
        try:
            try:
                resolved = resolve_workspace_path(workspace.project_root, path)
            except PathSecurityError as e:
                return ToolResult.fail(
                    display=format_block("read_file", "FAIL", {"path": path, "reason": str(e)}),
                    hint="路径被安全策略拦截",
                )
            full = Path(resolved)
            if not full.is_file():
                return ToolResult.fail(
                    display=format_block(
                        "read_file",
                        "FAIL",
                        {"path": path, "reason": "not found"},
                        hint="glob_files 或 search_code 确认路径",
                    )
                )
            # cache for unchanged files (mtime-keyed)
            cached = cache_get(workspace.project_root, path)
            if cached and not (end and end > 0) and not (start and int(start) > 1):
                raw, meta = cached
                lines = raw.splitlines()
                total = len(lines)
                # still apply outline logic below using cached text
            else:
                raw = full.read_text(encoding="utf-8", errors="replace")
                lines = raw.splitlines()
                total = len(lines)
                try:
                    cache_put(workspace.project_root, path, raw)
                except Exception:
                    pass
            start = int(start) if start else 1
            end = int(end) if end else 0

            # Explicit range
            if end and end > 0:
                lo = max(1, start)
                hi = min(total, end)
                chunk = lines[lo - 1 : hi]
                numbered = "\n".join(f"{lo + i}| {ln}" for i, ln in enumerate(chunk))
                body = f"{path} L{lo}-{hi}/{total}\n{numbered}"
                return ToolResult.ok(
                    display=format_block(
                        "read_file",
                        "OK",
                        {"path": path, "lines": total, "mode": "range"},
                        body,
                    ),
                    payload={"path": path, "lines": total, "mode": "range"},
                )

            # Large file without range -> outline
            if total > 150 and start <= 1:
                outline_lines = []
                if path.endswith(".py"):
                    try:
                        import ast as _ast
                        tree = _ast.parse(raw)
                        n = 0
                        for node in tree.body:
                            if isinstance(node, (_ast.FunctionDef, _ast.AsyncFunctionDef, _ast.ClassDef)):
                                n += 1
                                end_l = getattr(node, "end_lineno", node.lineno) or node.lineno
                                kind = "class" if isinstance(node, _ast.ClassDef) else "def"
                                outline_lines.append(
                                    f"[{n}] {kind} {node.name} (L{node.lineno}-{end_l})"
                                )
                    except Exception:
                        outline_lines = []
                if not outline_lines:
                    # anchor every 50 lines
                    for i in range(0, total, 50):
                        outline_lines.append(f"[L{i+1}] {lines[i][:80]}")
                body = "\n".join(outline_lines[:40])
                if total > 150:
                    body += f"\n... ({total} lines total)"
                return ToolResult.ok(
                    display=format_block(
                        "read_file",
                        "OK",
                        {"path": path, "lines": total, "mode": "outline"},
                        body,
                        hint='read_function(path, name) 或 read_file(path, start, end)',
                    ),
                    payload={"path": path, "lines": total, "mode": "outline", "outline": outline_lines[:40]},
                )

            lo = max(1, start)
            chunk = lines[lo - 1 :]
            numbered = "\n".join(f"{lo + i}| {ln}" for i, ln in enumerate(chunk))
            body = f"{path} L{lo}-{total}/{total}\n{numbered}"
            return ToolResult.ok(
                display=format_block(
                    "read_file",
                    "OK",
                    {"path": path, "lines": total, "mode": "full"},
                    body if total <= 500 else _truncate_head(body),
                ),
                payload={"path": path, "lines": total, "mode": "full"},
            )
        except Exception as e:
            return ToolResult.fail(
                display=format_block("read_file", "FAIL", {"path": path, "reason": str(e)})
            )


    def search_code(pattern: str, path: str = ".") -> ToolResult:
        try:
            try:
                resolve_workspace_path(workspace.project_root, path)
            except PathSecurityError as e:
                return ToolResult.fail(
                    display=format_block("search_code", "FAIL", {"path": path, "reason": str(e)}),
                    hint="路径被安全策略拦截",
                )
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
        """执行 shell；保留尾部输出，并提取 Error/Traceback 切片。"""
        try:
            danger = is_dangerous_command(cmd)
            if danger:
                return ToolResult.fail(
                    display=format_block(
                        "run_command", "FAIL", {"cmd": cmd, "reason": danger},
                        hint="命令被安全策略拦截",
                    )
                )
            r = subprocess.run(
                cmd,
                shell=True,
                cwd=workspace.project_root,
                capture_output=True,
                text=True,
                timeout=int(timeout) if timeout else 60,
            )
            out = (r.stdout or "") + (r.stderr or "")
            tail = _truncate(out)
            slices = error_slices(out)
            body = tail
            if slices:
                body = tail + "\n--- ERROR_SLICES ---\n" + slices
            ok = r.returncode == 0
            # learn test command
            if "pytest" in cmd or "npm test" in cmd or "cargo test" in cmd:
                try:
                    update_memory(workspace.project_root, test_command=cmd.strip())
                except Exception:
                    pass
            return ToolResult.ok(
                display=format_block(
                    "run_command",
                    "OK" if ok else "FAIL",
                    {"cmd": cmd, "exit": r.returncode},
                    body,
                    hint="" if ok else "看 ERROR_SLICES / 尾部输出",
                ),
                payload={"returncode": r.returncode, "cmd": cmd},
            ) if ok else ToolResult.fail(
                display=format_block(
                    "run_command",
                    "FAIL",
                    {"cmd": cmd, "exit": r.returncode},
                    body,
                    hint="看 ERROR_SLICES；或 run_test_structured",
                ),
                payload={"returncode": r.returncode, "cmd": cmd},
            )
        except Exception as e:
            return ToolResult.fail(
                display=decorate_fail_message(format_block("run_command", "FAIL", {"cmd": cmd, "reason": str(e)}), e)
            )


    def read_function(path: str, symbol_name: str) -> ToolResult:
        """只读取指定函数/类的源码（基于符号索引或单文件 AST）。"""
        try:
            from forge.core.symbol_index import lookup_function_range
            rng = lookup_function_range(workspace.project_root, path, symbol_name)
            if not rng:
                return ToolResult.fail(
                    display=(
                        f"在 {path} 中未找到 '{symbol_name}'\n"
                        f"建议: find_symbol_definition('{symbol_name}') 确认位置。"
                    )
                )
            start_line, end_line = rng
            target = Path(workspace.project_root) / path
            lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
            # 1-based inclusive
            chunk = lines[start_line - 1 : end_line]
            numbered = "\n".join(f"{start_line + i}| {ln}" for i, ln in enumerate(chunk))
            display = f"{path} :: {symbol_name} (L{start_line}-{end_line})\n{numbered}"
            _log("read_function", {"path": path, "symbol": symbol_name}, True)
            return ToolResult.ok(
                display=display,
                payload={
                    "path": path,
                    "symbol": symbol_name,
                    "start_line": start_line,
                    "end_line": end_line,
                    "content": "\n".join(chunk),
                },
            )
        except Exception as e:
            return ToolResult.fail(display=f"read_function 失败: {e}")

    def rebuild_symbol_index() -> ToolResult:
        """强制重建全仓符号索引（.forge/symbols.json）。"""
        try:
            from forge.core.symbol_index import build_symbol_index
            data = build_symbol_index(workspace.project_root, force=True)
            n_sym = len(data.get("symbols") or {})
            n_file = len(data.get("files") or {})
            display = f"RESULT: symbol_index rebuilt files={n_file} symbol_names={n_sym}"
            return ToolResult.ok(display=display, payload={"files": n_file, "symbols": n_sym})
        except Exception as e:
            return ToolResult.fail(display=f"rebuild_symbol_index 失败: {e}")

    def resolve_path_object(path: str) -> ToolResult:
        """文件路径 → Veritas ObjectId（查 ObjectPathMap）。"""
        try:
            if world_runtime is None:
                return ToolResult.fail(
                    display="resolve_path_object 失败: WorldRuntime 未绑定\n建议: 确认 Runtime 已启动 Veritas。"
                )
            path_n = path.replace("\\\\", "/").lstrip("./")
            oid = None
            path_map = getattr(world_runtime, "_path_map", None)
            if path_map is not None and hasattr(path_map, "find_object_id"):
                oid = path_map.find_object_id(path_n)
                if oid is None:
                    oid = path_map.find_object_id(path)
            if oid is None and hasattr(world_runtime, "find_object_id_for_path"):
                oid = world_runtime.find_object_id_for_path(path_n)
            if oid is None:
                return ToolResult.fail(
                    display=(
                        f"路径 '{path}' 未找到对应 ObjectId\n"
                        f"可能原因: 文件尚未经 create_file 进入 World，或 path map 未重建。\n"
                        f"建议: list_world_objects 或先 create_file。"
                    )
                )
            display = f"RESULT: path={path_n} object_id={int(oid)}"
            return ToolResult.ok(
                display=display,
                payload={"path": path_n, "object_id": int(oid)},
            )
        except Exception as e:
            return ToolResult.fail(display=f"resolve_path_object 失败: {e}")



    def glob_files(pattern: str, max_results: int = 200) -> ToolResult:
        """按 glob 模式列出文件（相对项目根）。"""
        try:
            root = Path(workspace.project_root).resolve()
            pat = pattern or "**/*"
            if pat.startswith("/"):
                return ToolResult.fail(display="glob_files: 请使用相对项目根的 pattern")
            matches = []
            for m in sorted(root.glob(pat)):
                if not m.is_file():
                    continue
                try:
                    rel = str(m.relative_to(root)).replace("\\", "/")
                except ValueError:
                    continue
                if any(
                    part in {".git", "__pycache__", ".venv", "venv", "node_modules", ".forge"}
                    for part in Path(rel).parts
                ):
                    continue
                matches.append(rel)
                if len(matches) >= max(1, int(max_results)):
                    break
            body = "\n".join(matches)
            display = f"RESULT: glob pattern={pat} count={len(matches)}\n{body}"
            if not matches:
                display = (
                    f"RESULT: glob pattern={pat} count=0\n(无匹配)\n"
                    f"建议: 放宽 pattern，如 **/*.py"
                )
            _log("glob_files", {"pattern": pat, "n": len(matches)}, True)
            return ToolResult.ok(
                display=display,
                payload={"pattern": pat, "files": matches, "count": len(matches)},
            )
        except Exception as e:
            return ToolResult.fail(display=f"glob_files failed: {e}")



    # Per-Runtime todo list (make_local_tools closure)
    _todo_items: list = []

    def todo_write(items: list) -> ToolResult:
        """Replace or update in-memory task list for the current agent session."""
        nonlocal _todo_items
        try:
            if not isinstance(items, list):
                return ToolResult.fail(display="todo_write: items 必须是列表 [{id?, content, status}]")
            allowed = {"pending", "in_progress", "done", "completed", "cancelled"}
            normalized = []
            for i, it in enumerate(items):
                if not isinstance(it, dict):
                    return ToolResult.fail(display=f"todo_write: items[{i}] 必须是 dict")
                content = it.get("content") or it.get("title") or ""
                if not content:
                    return ToolResult.fail(display=f"todo_write: items[{i}] 需要 content")
                status = (it.get("status") or "pending").lower()
                if status == "completed":
                    status = "done"
                if status not in allowed:
                    return ToolResult.fail(
                        display=f"todo_write: status 必须是 pending|in_progress|done，收到 {status}"
                    )
                tid = it.get("id") or str(i + 1)
                normalized.append({"id": str(tid), "content": str(content), "status": status})
            _todo_items = normalized
            lines = ["RESULT: todo updated"]
            for it in _todo_items:
                mark = {"pending": "[ ]", "in_progress": "[~]", "done": "[x]", "cancelled": "[-]"}.get(
                    it["status"], "[ ]"
                )
                lines.append(f"  {mark} {it['id']}. {it['content']} ({it['status']})")
            return ToolResult.ok(
                display="\n".join(lines),
                payload={"todos": list(_todo_items)},
            )
        except Exception as e:
            return ToolResult.fail(display=f"todo_write failed: {e}")

    def todo_list() -> ToolResult:
        """Show current in-memory todos."""
        if not _todo_items:
            return ToolResult.ok(display="RESULT: todo empty\n(无任务)", payload={"todos": []})
        lines = ["RESULT: todo list"]
        for it in _todo_items:
            mark = {"pending": "[ ]", "in_progress": "[~]", "done": "[x]", "cancelled": "[-]"}.get(
                it["status"], "[ ]"
            )
            lines.append(f"  {mark} {it['id']}. {it['content']} ({it['status']})")
        return ToolResult.ok(display="\n".join(lines), payload={"todos": list(_todo_items)})

    def web_fetch(url: str, max_chars: int = 5000) -> ToolResult:
        """Fetch http(s) URL text (no JS). urllib only."""
        import re
        import urllib.error
        import urllib.request
        from html.parser import HTMLParser

        class _TextExtractor(HTMLParser):
            def __init__(self):
                super().__init__()
                self.parts: list[str] = []
                self._skip = 0

            def handle_starttag(self, tag, attrs):
                if tag in ("script", "style", "noscript"):
                    self._skip += 1

            def handle_endtag(self, tag):
                if tag in ("script", "style", "noscript") and self._skip:
                    self._skip -= 1

            def handle_data(self, data):
                if self._skip:
                    return
                t = data.strip()
                if t:
                    self.parts.append(t)

        try:
            u = (url or "").strip()
            if not (u.startswith("http://") or u.startswith("https://")):
                return ToolResult.fail(display="web_fetch: 仅支持 http/https URL")
            req = urllib.request.Request(
                u,
                headers={"User-Agent": "ForgeAgent/1.0"},
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                raw = resp.read(max(1000, int(max_chars) * 4))
                ctype = (resp.headers.get("Content-Type") or "").lower()
            try:
                text = raw.decode("utf-8", errors="replace")
            except Exception:
                text = raw.decode("latin-1", errors="replace")
            if "html" in ctype or text.lstrip().lower().startswith("<!doctype") or "<html" in text[:200].lower():
                parser = _TextExtractor()
                try:
                    parser.feed(text)
                    text = "\n".join(parser.parts)
                except Exception:
                    text = re.sub(r"<[^>]+>", " ", text)
            text = re.sub(r"\n{3,}", "\n\n", text).strip()
            truncated = text[: int(max_chars)]
            if len(text) > int(max_chars):
                truncated += "\n...(truncated)"
            return ToolResult.ok(
                display=f"RESULT: url={u} chars={len(truncated)}\n{truncated}",
                payload={"url": u, "chars": len(truncated), "text": truncated},
            )
        except urllib.error.HTTPError as e:
            return ToolResult.fail(display=f"web_fetch HTTP {e.code}: {url}\n建议: 检查 URL 或稍后重试。")
        except Exception as e:
            return ToolResult.fail(display=f"web_fetch failed: {e}\n建议: 确认网络与 URL。")



    def project_memory() -> ToolResult:
        """返回项目记忆（测试命令、最近文件等）。"""
        data = load_memory(workspace.project_root)
        if not data:
            return ToolResult.ok(
                display=format_block("project_memory", "OK", {"empty": True}, "(empty)"),
                payload={},
            )
        body = "\n".join(f"{k}: {v}" for k, v in data.items())
        return ToolResult.ok(
            display=format_block("project_memory", "OK", {"keys": len(data)}, body),
            payload=data,
        )



    def session_changes() -> ToolResult:
        """本会话修改清单（path / tx / summary）。"""
        body = format_session_changes()
        items = list_changes()
        return ToolResult.ok(
            display=format_block(
                "session_changes",
                "OK",
                {"count": len(items)},
                body,
                hint="用户问改了哪些文件时优先用本工具",
            ),
            payload={"changes": items},
        )


    def post_toot(text: str, visibility: str = "unlisted") -> ToolResult:
        """发一条 Mastodon 嘟文。非强制：想发就调。"""
        try:
            from forge.adapters.mastodon import MastodonClient, is_configured
            if not is_configured():
                return ToolResult.fail(
                    display=format_block("post_toot", "FAIL", {"reason": "未配置 MASTODON_BASE_URL / MASTODON_ACCESS_TOKEN"}),
                    hint="export 后重试；Token 勿写入源码",
                )
            client = MastodonClient()
            data = client.post_status(text, visibility=visibility)
            url = data.get("url") or data.get("uri") or ""
            return ToolResult.ok(
                display=format_block("post_toot", "OK", {"url": url, "id": data.get("id"), "visibility": visibility}),
                payload={"mutation": True, "url": url, "id": data.get("id")},
            )
        except Exception as e:
            return ToolResult.fail(display=format_block("post_toot", "FAIL", {"reason": str(e)}))

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
        "run_type_check": run_type_check,
        "get_context_budget": get_context_budget,
        "inspect_last_intent": inspect_last_intent,
        "list_files": list_files,
        "glob_files": glob_files,
        "todo_write": todo_write,
        "todo_list": todo_list,
        "web_fetch": web_fetch,
        "project_memory": project_memory,
        "session_changes": session_changes,
        "read_file": read_file,
        "read_function": read_function,
        "search_code": search_code,
        "git_diff": git_diff,
        "git_log": git_log,
        "run_single_test": run_single_test,
        "run_command": run_command,
        "post_toot": post_toot,
        "world_info": world_info,
        "list_world_objects": list_world_objects,
        "get_world_object": get_world_object,
        "list_world_links": list_world_links,
        "resolve_path_object": resolve_path_object,
        "rebuild_symbol_index": rebuild_symbol_index,
    }

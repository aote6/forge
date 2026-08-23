"""文件读取与内容类只读工具。"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import sys
from pathlib import Path

from forge.adapters.base import ToolResult
from forge.core.security import resolve_workspace_path, PathSecurityError
from forge.tools._common import _log, _truncate, _truncate_head
from forge.tools.display import format_block
from forge.tools.read_cache import get as cache_get, put as cache_put


def make_read_tools(workspace) -> dict:
    def get_repo_map(root_dir: str = ".", max_tokens: int = 1500) -> ToolResult:
        """使用 Python 原生 ast 提取代码签名，压缩上下文。零额外依赖。"""
        try:
            root = Path(workspace.project_root) / root_dir
            if not root.exists():
                return ToolResult.fail(display=f"目录不存在: {root_dir}")
            summary_lines = []
            skipped = []
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
                    except Exception as e:
                        skipped.append(f"{rel_path}: {e}")
                        continue
            result = "\n".join(summary_lines)
            if len(result) > max_tokens * 4:
                result = result[: max_tokens * 4] + "\n... [Repo Map Truncated]"
            display = result or "No Python signatures found."
            if skipped:
                display += (
                    "\n\n⚠ skipped unparsable files:\n"
                    + "\n".join(f"  - {s}" for s in skipped[:20])
                )
            _log("get_repo_map", {"root_dir": root_dir, "max_tokens": max_tokens}, True)
            return ToolResult.ok(
                display=display,
                payload={"mutation": False, "file_count": len(summary_lines), "skipped_files": skipped},
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
                            except Exception as e:
                                print(f"[local_tools] cache_put failed: {e}", file=sys.stderr)
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
                except Exception as e:
                    print(f"[local_tools] summarize_file 读取缓存失败: {e}", file=sys.stderr)

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
                except Exception as e:
                    print(f"[local_tools] cache_put failed: {e}", file=sys.stderr)
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

    return {
        "read_file": read_file,
        "read_function": read_function,
        "read_files": read_files,
        "read_file_with_lines": read_file_with_lines,
        "preview_line_mutation": preview_line_mutation,
        "get_symbol_line_range": get_symbol_line_range,
        "extract_code_skeleton": extract_code_skeleton,
        "summarize_file": summarize_file,
        "get_repo_map": get_repo_map,
        "list_files": list_files,
        "get_context_budget": get_context_budget,
    }

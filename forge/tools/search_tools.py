"""搜索与符号导航类只读工具。"""

from __future__ import annotations

import ast
import json
import os
import re
from pathlib import Path

from forge.adapters.base import ToolResult
from forge.core.security import resolve_workspace_path, PathSecurityError
from forge.tools._common import _log, _truncate
from forge.tools.display import format_block

# AST 缓存：get_call_chain 避免反复解析同一文件（键含 mtime_ns + size，变动自动失效）。
_AST_CACHE: dict = {}
_AST_CACHE_MAX = 256


def _parse_cached(full_path: Path, rel_path: str):
    """按路径 + mtime_ns/size 缓存解析后的 AST；文件变动自动失效。"""
    try:
        st = full_path.stat()
        sig = (st.st_mtime_ns, st.st_size)
    except OSError:
        sig = None
    key = (str(full_path), sig)
    tree = _AST_CACHE.get(key)
    if tree is not None:
        return tree
    with open(full_path, "r", encoding="utf-8") as file:
        tree = ast.parse(file.read(), filename=rel_path)
    if sig is not None:
        if len(_AST_CACHE) >= _AST_CACHE_MAX:
            _AST_CACHE.clear()
        _AST_CACHE[key] = tree
    return tree


def make_search_tools(workspace) -> dict:
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
            skipped: list[str] = []

            # 优先用符号索引定位定义，避免第一遍全仓 AST 遍历
            hits = []
            try:
                from forge.core.symbol_index import lookup_symbol
                hits = lookup_symbol(workspace.project_root, symbol_name, exact=True)
                # 与原实现一致：只认函数/类定义（索引还会收录变量等）
                hits = [h for h in hits if h.get("kind") in ("function", "class")]
            except Exception:
                hits = []

            if hits:
                definition_paths = [h.get("path") for h in hits]
                # 多次命中时取最后一个定义位置（与原实现一致）
                target_file = hits[-1].get("path")
                target_line = hits[-1].get("start_line")
                # 收集被调用者：只解析定义所在文件
                for rel_path in dict.fromkeys(definition_paths):
                    try:
                        tree = _parse_cached(Path(workspace.project_root) / rel_path, rel_path)
                        for node in ast.walk(tree):
                            if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == symbol_name:
                                # 收集该函数内部调用
                                for sub in ast.walk(node):
                                    if isinstance(sub, ast.Call):
                                        if isinstance(sub.func, ast.Name):
                                            callees.add(sub.func.id)
                                        elif isinstance(sub.func, ast.Attribute):
                                            callees.add(sub.func.attr)
                    except Exception as e:
                        skipped.append(f"{rel_path}: {e}")

            # 收集调用者：仍需一遍全仓遍历（符号索引不存调用点）
            for root, _, files in os.walk(workspace.project_root):
                if any(skip in root for skip in (".git", "__pycache__", ".venv", ".pytest_cache")):
                    continue
                for f in files:
                    if not f.endswith(".py"):
                        continue
                    full_path = Path(root) / f
                    rel_path = str(full_path.relative_to(workspace.project_root))
                    try:
                        tree = _parse_cached(full_path, rel_path)
                        for node in ast.walk(tree):
                            if isinstance(node, ast.Call):
                                if isinstance(node.func, ast.Name) and node.func.id == symbol_name:
                                    callers.add(f"{rel_path}:{node.lineno}")
                                elif isinstance(node.func, ast.Attribute) and node.func.attr == symbol_name:
                                    callers.add(f"{rel_path}:{node.lineno}")
                    except Exception as e:
                        skipped.append(f"{rel_path}: {e}")
                        continue

            # 索引未命中时回退：第一遍全仓找定义 + 收集被调用者（保持原行为）
            if not target_file:
                for root, _, files in os.walk(workspace.project_root):
                    if any(skip in root for skip in (".git", "__pycache__", ".venv", ".pytest_cache")):
                        continue
                    for f in files:
                        if not f.endswith(".py"):
                            continue
                        full_path = Path(root) / f
                        rel_path = str(full_path.relative_to(workspace.project_root))
                        try:
                            tree = _parse_cached(full_path, rel_path)
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
                        except Exception as e:
                            skipped.append(f"{rel_path}: {e}")
                            continue

            _log("get_call_chain", {"symbol": symbol_name}, True)
            output = f"符号: {symbol_name}\n"
            if target_file:
                output += f"定义位置: {target_file}:{target_line}\n\n"
            output += f"调用者 ({len(callers)}):\n"
            output += ("\n".join(f"  ← {c}" for c in sorted(callers)) if callers else "  (无直接调用者)\n")
            output += f"\n被调用 ({len(callees)}):\n"
            output += ("\n".join(f"  → {c}" for c in sorted(callees)) if callees else "  (无直接调用)")
            if skipped:
                output += (
                    "\n\n⚠ skipped unparsable files:\n"
                    + "\n".join(f"  - {s}" for s in sorted(set(skipped))[:20])
                )
            return ToolResult.ok(
                display=output,
                payload={"mutation": False, "callers": sorted(callers), "callees": sorted(callees), "skipped_files": sorted(set(skipped))},
            )
        except Exception as e:
            _log("get_call_chain", {"symbol": symbol_name}, False, str(e))
            return ToolResult.fail(display=f"get_call_chain 失败: {e}")

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

    def search_history(query: str, max_results: int = 5) -> ToolResult:
        """仅本会话/对话历史日志。不是项目工作历史；默认不用于「今天/最近做了什么」。"""
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

    def glob_files(pattern: str, max_results: int = 200) -> ToolResult:
        """按 glob 模式列出文件（相对项目根）。

        默认跳过 .forge（及 .git/venv 等噪声目录）。若 pattern 显式以
        .forge 或 .forge/ 开头，则允许列出 .forge 下的匹配文件。
        """
        try:
            root = Path(workspace.project_root).resolve()
            pat = pattern or "**/*"
            if pat.startswith("/"):
                return ToolResult.fail(display="glob_files: 请使用相对项目根的 pattern")
            # Explicit .forge targeting: allow .forge path segments; else keep hidden.
            pat_norm = pat.replace("\\", "/").strip()
            while pat_norm.startswith("./"):
                pat_norm = pat_norm[2:]
            explicit_forge = pat_norm == ".forge" or pat_norm.startswith(".forge/")
            exclude_parts = {".git", "__pycache__", ".venv", "venv", "node_modules"}
            if not explicit_forge:
                exclude_parts = exclude_parts | {".forge"}
            matches = []
            for m in sorted(root.glob(pat)):
                if not m.is_file():
                    continue
                try:
                    rel = str(m.relative_to(root)).replace("\\", "/")
                except ValueError:
                    continue
                if any(part in exclude_parts for part in Path(rel).parts):
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

    return {
        "search_code": search_code,
        "glob_files": glob_files,
        "find_symbol_definition": find_symbol_definition,
        "get_call_chain": get_call_chain,
        "rebuild_symbol_index": rebuild_symbol_index,
        "search_history": search_history,
        "inspect_last_intent": inspect_last_intent,
    }

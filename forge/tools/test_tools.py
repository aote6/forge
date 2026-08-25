"""测试运行与诊断类工具。"""

from __future__ import annotations

import ast
import json
import os
import subprocess
from pathlib import Path

from forge.adapters.base import ToolResult
from forge.tools._common import _log, _truncate

def _persist_pytest_result(project_root: str, *, command: str, target: str, returncode: int, out: str) -> None:
    """Write .forge/last_test_result.json from a completed pytest run (no invention)."""
    try:
        from forge.tools.project_review import save_last_test_result
        import re as _re
        passed = failed = None
        m = _re.search(r"(\d+)\s+passed", out)
        if m:
            passed = int(m.group(1))
        m = _re.search(r"(\d+)\s+failed", out)
        if m:
            failed = int(m.group(1))
        if failed is None:
            failed = 0 if returncode == 0 else (1 if passed is not None else None)
        status = "passed" if returncode == 0 else "failed"
        save_last_test_result(
            project_root,
            {
                "command": command,
                "target": target,
                "returncode": returncode,
                "passed": passed,
                "failed": failed if failed is not None else (0 if returncode == 0 else 1),
                "status": status,
            },
        )
    except Exception as e:
        import sys
        print(f"[test_tools] persist last_test_result failed: {e}", file=sys.stderr)


def make_test_tools(workspace) -> dict:
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
            _persist_pytest_result(
                workspace.project_root,
                command=" ".join(cmd),
                target=target,
                returncode=r.returncode,
                out=out,
            )
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
                    except Exception as e:
                        errors.append({
                            "file": os.path.relpath(p, workspace.project_root),
                            "error": f"read/parse error: {e}",
                        })
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

    def run_single_test(path: str, timeout: int = 60) -> ToolResult:
        """Run a single pytest file or test node (argv list, no shell)."""
        try:
            # path is one pytest target (file or node id). Pass as a single argv
            # element so shell metacharacters are never interpreted.
            argv = ["python3", "-m", "pytest", str(path), "-v", "--tb=short"]
            result = subprocess.run(
                argv,
                shell=False,
                cwd=workspace.project_root,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            cmd_display = " ".join(argv)
            display = f"$ {cmd_display}\n[exit {result.returncode}]\n"
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

    return {
        "run_test_structured": run_test_structured,
        "run_diagnostics": run_diagnostics,
        "run_type_check": run_type_check,
        "list_tests": list_tests,
        "run_single_test": run_single_test,
    }

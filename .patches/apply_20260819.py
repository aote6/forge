import sys
from pathlib import Path

ROOT = Path.home() / "forge"

def patch(rel_path, old, new, label):
    fp = ROOT / rel_path
    text = fp.read_text(encoding="utf-8")
    n = text.count(old)
    if n != 1:
        print(f"[SKIP] {label}: 匹配到 {n} 处（需要恰好1处），未修改。请手动检查 {rel_path}")
        return False
    fp.write_text(text.replace(old, new, 1), encoding="utf-8")
    print(f"[OK] {label}")
    return True

# 1. runtime.py: 熔断签名对内容不敏感 -> 加content/operations hash
patch(
    "forge/runtime.py",
    '''        if tool_name in ("write_file", "modify_file"):
            path = str(args.get("path") or "")
            return f"{tool_name}:{path}"
''',
    '''        if tool_name == "write_file":
            import hashlib
            path = str(args.get("path") or "")
            content = str(args.get("content") or "")
            h = hashlib.sha1(content.encode("utf-8", errors="replace")).hexdigest()[:12]
            return f"write_file:{path}:{h}"
        if tool_name == "modify_file":
            import hashlib
            path = str(args.get("path") or "")
            ops = args.get("operations") or []
            ops_json = json.dumps(ops, sort_keys=True, ensure_ascii=False, default=str)
            h = hashlib.sha1(ops_json.encode("utf-8", errors="replace")).hexdigest()[:12]
            return f"modify_file:{path}:{h}"
''',
    "runtime.py _args_signature 内容感知",
)

# 2. related_tests.py: symbols_from_edit 未真正优先"变化的符号"
patch(
    "forge/tools/related_tests.py",
    '''def symbols_from_edit(old: str, new: str, max_n: int = 5) -> list[str]:
    """Extract likely def/class names touched by an edit (for coverage_hint)."""
    import re
    names: list[str] = []
    for blob in (old or "", new or ""):
        for m in re.finditer(r"^\\s*(?:async\\s+)?def\\s+([A-Za-z_]\\w*)", blob, re.M):
            names.append(m.group(1))
        for m in re.finditer(r"^\\s*class\\s+([A-Za-z_]\\w*)", blob, re.M):
            names.append(m.group(1))
    # prefer names that appear in only one side or near changed lines
    seen = []
    for n in names:
        if n not in seen:
            seen.append(n)
    return seen[:max_n]
''',
    '''def symbols_from_edit(old: str, new: str, max_n: int = 5) -> list[str]:
    """Extract likely def/class names touched by an edit (for coverage_hint)."""
    import re

    def _extract(blob: str) -> list[str]:
        names: list[str] = []
        for m in re.finditer(r"^\\s*(?:async\\s+)?def\\s+([A-Za-z_]\\w*)", blob, re.M):
            names.append(m.group(1))
        for m in re.finditer(r"^\\s*class\\s+([A-Za-z_]\\w*)", blob, re.M):
            names.append(m.group(1))
        return names

    old_names = _extract(old or "")
    new_names = _extract(new or "")
    old_set, new_set = set(old_names), set(new_names)
    # 真正变化的符号优先(对称差)，未变的公共符号垫后补位
    changed = [n for n in new_names if n not in old_set] + \\
              [n for n in old_names if n not in new_set]
    common = [n for n in new_names if n in old_set]

    seen: list[str] = []
    for n in changed + common:
        if n not in seen:
            seen.append(n)
    return seen[:max_n]
''',
    "related_tests.py symbols_from_edit 优先变化符号",
)

# 3a. session_changes.py record(): summary换行清洗 + persist失败不再裸吞
patch(
    "forge/tools/session_changes.py",
    '''def record(
    path: str,
    *,
    tx_id: Any = None,
    tool: str = "",
    summary: str = "",
    project_root: str | None = None,
) -> None:
    entry = {
        "ts": time.time(),
        "path": path,
        "tx": tx_id,
        "tool": tool,
        "summary": (summary or "")[:200],
    }
    _LOG.append(entry)
    if project_root:
        try:
            _persist(project_root)
        except Exception:
            pass
''',
    '''def record(
    path: str,
    *,
    tx_id: Any = None,
    tool: str = "",
    summary: str = "",
    project_root: str | None = None,
) -> None:
    entry = {
        "ts": time.time(),
        "path": path,
        "tx": tx_id,
        "tool": tool,
        "summary": (summary or "")[:200].replace("\\n", " ").replace("\\r", " "),
    }
    _LOG.append(entry)
    if project_root:
        try:
            _persist(project_root, entry)
        except Exception as e:
            print(f"[session_changes] persist failed: {e}", file=sys.stderr)
''',
    "session_changes.py record() 清洗+日志",
)

# 3b. _persist: 全量重写 -> 追加写 jsonl (O(n^2) -> O(1)/call)
patch(
    "forge/tools/session_changes.py",
    '''def _persist(project_root: str) -> None:
    d = Path(project_root) / ".forge"
    d.mkdir(parents=True, exist_ok=True)
    path = d / "session_changes.json"
    path.write_text(json.dumps(_LOG, ensure_ascii=False, indent=2), encoding="utf-8")
''',
    '''def _persist(project_root: str, entry: dict[str, Any] | None = None) -> None:
    """Append-only: 只写这一条,不再每次全量重写整个日志."""
    d = Path(project_root) / ".forge"
    d.mkdir(parents=True, exist_ok=True)
    path = d / "session_changes.jsonl"
    line = json.dumps(entry if entry is not None else (_LOG[-1] if _LOG else {}), ensure_ascii=False)
    with path.open("a", encoding="utf-8") as f:
        f.write(line + "\\n")
''',
    "session_changes.py _persist 改为追加写(json->jsonl)",
)

# 3c. load_into_memory: 跟着换成读 jsonl，异常不再裸吞
patch(
    "forge/tools/session_changes.py",
    '''def load_into_memory(project_root: str) -> None:
    """Optional: load previous session file (does not auto-merge unless called)."""
    path = Path(project_root) / ".forge" / "session_changes.json"
    if not path.is_file():
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            _LOG.clear()
            _LOG.extend(data[-50:])
    except Exception:
        pass
''',
    '''def load_into_memory(project_root: str) -> None:
    """Optional: load previous session file (does not auto-merge unless called)."""
    path = Path(project_root) / ".forge" / "session_changes.jsonl"
    if not path.is_file():
        return
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        entries: list[dict[str, Any]] = []
        for ln in lines[-50:]:
            ln = ln.strip()
            if not ln:
                continue
            try:
                entries.append(json.loads(ln))
            except Exception:
                continue
        _LOG.clear()
        _LOG.extend(entries)
    except Exception as e:
        print(f"[session_changes] load failed: {e}", file=sys.stderr)
''',
    "session_changes.py load_into_memory 读 jsonl",
)

# 3d. import sys（session_changes.py 原本没 import sys，现在用到了）
patch(
    "forge/tools/session_changes.py",
    '''import json
import time
from pathlib import Path
from typing import Any
''',
    '''import json
import sys
import time
from pathlib import Path
from typing import Any
''',
    "session_changes.py 补 import sys",
)

# 4. intent_tools.py undo_last_tx: 两处裸 except:pass 改为记日志
patch(
    "forge/tools/intent_tools.py",
    '''            for path in info.get("paths") or []:
                try:
                    cache_invalidate(root, path)
                except Exception:
                    pass
            try:
                record_session_change(
                    ",".join(info.get("paths") or []) or "(undo)",
                    tool="undo_last_tx",
                    tx_id=info.get("undone_tx"),
                    summary="shadow revert",
                    project_root=root,
                )
            except Exception:
                pass
''',
    '''            for path in info.get("paths") or []:
                try:
                    cache_invalidate(root, path)
                except Exception as e:
                    print(f"[undo_last_tx] cache_invalidate failed for {path}: {e}", file=sys.stderr)
            try:
                record_session_change(
                    ",".join(info.get("paths") or []) or "(undo)",
                    tool="undo_last_tx",
                    tx_id=info.get("undone_tx"),
                    summary="shadow revert",
                    project_root=root,
                )
            except Exception as e:
                print(f"[undo_last_tx] record_session_change failed: {e}", file=sys.stderr)
''',
    "intent_tools.py undo_last_tx 日志化",
)

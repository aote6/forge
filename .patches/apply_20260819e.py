import re
from pathlib import Path

ROOT = Path("/data/data/com.termux/files/home/forge/forge")

def patch(path: Path, old: str, new: str, label: str):
    text = path.read_text(encoding="utf-8")
    if old not in text:
        print(f"[SKIP] {label}: 未找到目标片段（可能已打过或代码已变）")
        return
    count = text.count(old)
    if count != 1:
        print(f"[WARN] {label}: 匹配到 {count} 处，跳过以防误改")
        return
    text = text.replace(old, new)
    path.write_text(text, encoding="utf-8")
    print(f"[OK] {label}")

# 1. runtime.py: todo_write 纳入 _CONFIRMATION_TOOLS
runtime_py = ROOT / "runtime.py"
patch(
    runtime_py,
    '_CONFIRMATION_TOOLS = {\n'
    '    "write_file", "modify_file", "undo_last_tx", "create_object",\n'
    '    "delete_file", "create_file", "unlink_objects", "link_objects",\n'
    '    "run_test_structured", "apply_patch", "edit_files_batch",\n'
    '}',
    '_CONFIRMATION_TOOLS = {\n'
    '    "write_file", "modify_file", "undo_last_tx", "create_object",\n'
    '    "delete_file", "create_file", "unlink_objects", "link_objects",\n'
    '    "run_test_structured", "apply_patch", "edit_files_batch", "todo_write",\n'
    '}',
    "runtime.py todo_write 纳入 _CONFIRMATION_TOOLS",
)

# 2. runtime.py: goal_clarify 裸 except 改 stderr 日志
patch(
    runtime_py,
    '            if user_looks_like_clarification(task):\n'
    '                mark_clarified()\n'
    '            elif needs_clarify(task):\n'
    '                messages.append(ForgeMessage(role="user", content=clarification_message()))\n'
    '        except Exception:\n'
    '            pass',
    '            if user_looks_like_clarification(task):\n'
    '                mark_clarified()\n'
    '            elif needs_clarify(task):\n'
    '                messages.append(ForgeMessage(role="user", content=clarification_message()))\n'
    '        except Exception as e:\n'
    '            import sys\n'
    '            print(f"[forge] goal_clarify unavailable: {e}", file=sys.stderr)',
    "runtime.py goal_clarify 裸except改stderr日志",
)

print("done")

from pathlib import Path

ROOT = Path.home() / "forge"

def patch(rel_path, old, new, label):
    fp = ROOT / rel_path
    text = fp.read_text(encoding="utf-8")
    n = text.count(old)
    if n != 1:
        print(f"[SKIP] {label}: 匹配到 {n} 处（需要恰好1处），未修改。")
        return False
    fp.write_text(text.replace(old, new, 1), encoding="utf-8")
    print(f"[OK] {label}")
    return True

patch(
    "forge/runtime.py",
    '''_CONFIRMATION_TOOLS = {
    "write_file", "modify_file", "undo_last_tx", "create_object",
    "delete_object", "freeze_object", "unlink_object", "link_objects",
    "run_test_structured", "apply_patch",
}''',
    '''_CONFIRMATION_TOOLS = {
    "write_file", "modify_file", "undo_last_tx", "create_object",
    "delete_file", "create_file", "unlink_objects", "link_objects",
    "run_test_structured", "apply_patch", "edit_files_batch",
}''',
    "runtime.py _CONFIRMATION_TOOLS 名单对齐 schemas.py 真实工具名",
)

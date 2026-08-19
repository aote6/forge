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

# system_prompt.py: 工具简写对不上真实工具名
patch(
    "forge/system_prompt.py",
    "探索: glob / search / read_function / read_file（大文件=大纲）",
    "探索: glob_files / search_code / read_function / read_file（大文件=大纲）",
    "system_prompt.py 工具名对齐真实schema",
)

# local_tools.py: read_files 批量读没走缓存，只在“整篇读取无行范围”时安全接入
patch(
    "forge/tools/local_tools.py",
    '''                try:
                    content = workspace.read_file(path, start_line or 1, end_line or 0)
                    header = f"--- {path}"''',
    '''                try:
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
                    header = f"--- {path}"''',
    "local_tools.py read_files 接入 read_cache（仅整篇读取时）",
)

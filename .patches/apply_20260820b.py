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
    '''        consecutive_failures = 0
        for s in reversed(history):
            if s == "fail":
                consecutive_failures += 1
            else:
                break

        if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            return ToolResult.fail(
                display=(
                    f"STOP_HINT: 同一调用已连续失败 {consecutive_failures} 次，已禁止再试。\n"
                    f"  {tool_call.name}({json.dumps(tool_call.arguments, ensure_ascii=False)})\n"
                    f"请换策略、read 核对，或直接问用户。不要继续微调 同一参数。"
                )
            )''',
    '''        consecutive_failures = 0
        last_kind = ""
        for s in reversed(history):
            if s.startswith("fail"):
                consecutive_failures += 1
                if not last_kind and ":" in s:
                    last_kind = s.split(":", 1)[1]
            else:
                break

        _KIND_ADVICE = {
            "type_mismatch": "参数结构反复不对，重新读一遍工具schema再改参数，不要靠猜。",
            "exception": "运行时异常反复出现，问题可能不在参数上，检查前置状态(文件是否存在/veritasd是否在线)。",
            "logic": "工具正常执行但业务上判定失败(如old_string未找到)，仔细核对返回里的HINT/NEAR_MISS。",
        }

        if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            advice = _KIND_ADVICE.get(last_kind, "请换策略、read 核对，或直接问用户。")
            return ToolResult.fail(
                display=(
                    f"STOP_HINT: 同一调用已连续失败 {consecutive_failures} 次(原因: {last_kind or chr(39)+chr(39)})，已禁止再试。\n"
                    f"  {tool_call.name}({json.dumps(tool_call.arguments, ensure_ascii=False)})\n"
                    f"{advice} 不要继续微调同一参数。"
                )
            )''',
    "runtime.py STOP_HINT 熔断消息加失败原因分类 + 计数逻辑改为startswith",
)

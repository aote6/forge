from pathlib import Path

fp = Path.home() / "forge" / "forge" / "runtime.py"
lines = fp.read_text(encoding="utf-8").splitlines(keepends=True)

# 定位关键行（基于你刚贴的行号：227=consecutive_failures=0, 229=if s == "fail"）
start_idx = None
for i, ln in enumerate(lines):
    if "consecutive_failures = 0" in ln:
        start_idx = i
        break

if start_idx is None:
    print("[FAIL] 找不到 'consecutive_failures = 0' 这一行，请手动检查")
else:
    # 找到这个block结束的位置：即 STOP_HINT return 语句结束的那个右括号+右括号那行
    # 用一个稳健标记：找到包含 "请换策略、read 核对" 的那一行，再往后找最近的 "            )" 收尾
    end_idx = None
    for j in range(start_idx, min(start_idx + 30, len(lines))):
        if "请换策略、read 核对" in lines[j]:
            # 往后找到该 return 语句的收尾（两个反括号后那行通常是 "            )\n" 然后 ")\n"）
            for k in range(j, min(j + 6, len(lines))):
                if lines[k].strip() == ")" and k > j:
                    end_idx = k
                    break
            break

    if end_idx is None:
        print("[FAIL] 找不到STOP_HINT block结尾，请手动检查，start_idx=", start_idx)
    else:
        indent = "        "  # 8 spaces, 方法体内一级缩进
        new_block = f'''{indent}consecutive_failures = 0
{indent}last_kind = ""
{indent}for s in reversed(history):
{indent}    if s.startswith("fail"):
{indent}        consecutive_failures += 1
{indent}        if not last_kind and ":" in s:
{indent}            last_kind = s.split(":", 1)[1]
{indent}    else:
{indent}        break

{indent}_KIND_ADVICE = {{
{indent}    "type_mismatch": "参数结构反复不对，重新读一遍工具schema再改参数，不要靠猜。",
{indent}    "exception": "运行时异常反复出现，问题可能不在参数上，检查前置状态(文件是否存在/veritasd是否在线)。",
{indent}    "logic": "工具正常执行但业务上判定失败(如old_string未找到)，仔细核对返回里的HINT/NEAR_MISS。",
{indent}}}

{indent}if consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
{indent}    advice = _KIND_ADVICE.get(last_kind, "请换策略、read 核对，或直接问用户。")
{indent}    return ToolResult.fail(
{indent}        display=(
{indent}            f"STOP_HINT: 同一调用已连续失败 {{consecutive_failures}} 次(原因: {{last_kind or '未知'}})，已禁止再试。\\n"
{indent}            f"  {{tool_call.name}}({{json.dumps(tool_call.arguments, ensure_ascii=False)}})\\n"
{indent}            f"{{advice}} 不要继续微调同一参数。"
{indent}        )
{indent})
'''
        new_lines = lines[:start_idx] + [new_block] + lines[end_idx + 1:]
        fp.write_text("".join(new_lines), encoding="utf-8")
        print(f"[OK] 替换了第 {start_idx+1} 到 {end_idx+1} 行")

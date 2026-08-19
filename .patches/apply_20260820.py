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

# ============================================================
# 补丁1: write_file 覆盖已存在文件时加前置提示（不拦截，只提示）
# ============================================================
patch(
    "forge/tools/intent_tools.py",
    '''            if result.success:
                mode = "overwrite" if oid is not None else "create_or_register"
                result.display = (
                    f"RESULT: path={path_n} mode={mode} object_id={result.payload.get('object_id')} "
                    f"tx={result.payload.get('tx_id')} version={result.payload.get('version')}\\n"
                    f"write_file ok: {path_n}"
                )''',
    '''            if result.success:
                mode = "overwrite" if oid is not None else "create_or_register"
                overwrite_hint = ""
                if mode == "overwrite" and old_content.strip() and old_content != new_content:
                    old_lines = old_content.count("\\n") + 1
                    overwrite_hint = (
                        f"\\nHINT: 覆盖了已存在文件({old_lines}行)。"
                        f"若只想改部分内容，下次可用 str_replace/modify_file 更安全。"
                    )
                result.display = (
                    f"RESULT: path={path_n} mode={mode} object_id={result.payload.get('object_id')} "
                    f"tx={result.payload.get('tx_id')} version={result.payload.get('version')}\\n"
                    f"write_file ok: {path_n}{overwrite_hint}"
                )''',
    "intent_tools.py write_file 覆盖已存在文件时加提示",
)

# ============================================================
# 补丁2: STOP_HINT 加粗粒度失败原因分类（参数不匹配 vs 运行时异常 vs 业务失败）
# ============================================================
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
                    f"STOP_HINT: 同一调用已连续失败 {consecutive_failures} 次，已禁止再试。\\n"
                    f"  {tool_call.name}({json.dumps(tool_call.arguments, ensure_ascii=False)})\\n"
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
                    f"STOP_HINT: 同一调用已连续失败 {consecutive_failures} 次(原因: {last_kind or '未知'})，已禁止再试。\\n"
                    f"  {tool_call.name}({json.dumps(tool_call.arguments, ensure_ascii=False)})\\n"
                    f"{advice} 不要继续微调同一参数。"
                )
            )''',
    "runtime.py STOP_HINT 熔断消息加失败原因分类",
)

patch(
    "forge/runtime.py",
    '''        try:
            result = fn(**tool_call.arguments)
            status = "success" if result.success else "fail"
            self.call_history.setdefault(sig, []).append(status)
            if not result.success and consecutive_failures >= 1:
                # after 1 prior fail, this is 2nd+ failure in a row for same sig
                prefix = (
                    f"STOP_HINT: 该调用已连续失败 {consecutive_failures + 1} 次。"
                    f"请换方向或问用户，勿重复同一操作。\\n"
                )
                if result.display and "STOP_HINT" not in result.display:
                    result.display = prefix + result.display
            return result
        except TypeError as e:
            self.call_history.setdefault(sig, []).append("fail")
            return ToolResult.fail(
                display=f"参数不匹配: {e}\\n收到的参数: {tool_call.arguments}"
            )
        except Exception as e:
            self.call_history.setdefault(sig, []).append("fail")
            return ToolResult.fail(display=f"工具执行异常: {type(e).__name__}: {e}")''',
    '''        try:
            result = fn(**tool_call.arguments)
            status = "success" if result.success else "fail:logic"
            self.call_history.setdefault(sig, []).append(status)
            if not result.success and consecutive_failures >= 1:
                # after 1 prior fail, this is 2nd+ failure in a row for same sig
                prefix = (
                    f"STOP_HINT: 该调用已连续失败 {consecutive_failures + 1} 次(原因: logic)。"
                    f"请换方向或问用户，勿重复同一操作。\\n"
                )
                if result.display and "STOP_HINT" not in result.display:
                    result.display = prefix + result.display
            return result
        except TypeError as e:
            self.call_history.setdefault(sig, []).append("fail:type_mismatch")
            return ToolResult.fail(
                display=f"参数不匹配: {e}\\n收到的参数: {tool_call.arguments}"
            )
        except Exception as e:
            self.call_history.setdefault(sig, []).append("fail:exception")
            return ToolResult.fail(display=f"工具执行异常: {type(e).__name__}: {e}")''',
    "runtime.py 失败状态记录附带原因分类(type_mismatch/exception/logic)",
)

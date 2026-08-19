# Forge 项目状态

## 生产路径

`Runtime.run` → 工具循环 → IntentExecutor → Veritas → Projection

## 本轮写代码能力升级

### P0
- 符号索引缓存 `forge/core/symbol_index.py` → `.forge/symbols.json`
- `find_symbol_definition` 查索引
- `resolve_path_object`；`modify_file` 自动解析 object_id
- `edit_files_batch` 多文件单事务；`modify_file` 支持多 operations
- mutation 工具 `require_confirm=False`（避免工具循环卡在确认）

### P1
- `run_type_check`（mypy/pyright/ast）
- `run_test_structured` 失败上下文（前后 5 行）
- `read_function`

### P2–P3
- 同轮多个 tool_calls 依次执行（模型并行请求已支持）
- mutation display 统一 `RESULT: ...`
- 对话自动写入 conversation_log.jsonl；失败提示含建议下一步

## 测试

`tests/test_coding_capability_upgrades.py` + 原有工具循环/Veritas 测试。

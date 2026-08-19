# Forge

Forge 是运行在 Veritas（确定性世界内核）之上的工程 Agent。

## 架构

```
LLM（工具循环）
  -> ToolExecutor
    -> IntentExecutor（事务）
      -> WorldSession -> veritasd -> Veritas Kernel
        -> Projection
```

生产路径唯一：`Runtime.run` → `_run_conversation`。

## 写代码能力（本轮增强）

| 能力 | 工具 / 机制 |
|------|-------------|
| 全仓符号索引 | `.forge/symbols.json`，`find_symbol_definition` / `rebuild_symbol_index` |
| 按符号读代码 | `read_function(path, symbol_name)` |
| 路径→ObjectId | `resolve_path_object`；`modify_file` 可省略 object_id |
| 多处/多文件编辑 | `modify_file` 多 operations；`edit_files_batch` 单事务 |
| 类型检查 | `run_type_check`（mypy/pyright/ast） |
| 测试失败上下文 | `run_test_structured` 附带失败行前后源码 |
| 会话记忆 | 自动写 `.forge/conversation_log.jsonl`，`search_history` 可查 |
| 结构化返回 | 关键 mutation 以 `RESULT: key=value` 开头 |

## 快速开始

```bash
export DEEPSEEK_API_KEY="你的key"
python3 dp.py
```

## 测试

```bash
python3 -m pytest -q
```

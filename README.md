# Forge

运行在 Veritas 上的工程 Agent：工具循环 + 事务突变。

## 架构

```
LLM → 精简工具集 → IntentExecutor → Veritas → Projection
```

## 工具设计（质量优先）

LLM 可见约 **25** 个工具（非 40+）：

| 类别 | 工具 |
|------|------|
| 探索 | glob_files, search_code, find_symbol_definition, read_file, read_function, get_repo_map |
| **编辑（首选）** | **str_replace**, **write_file** |
| 编辑（高级） | modify_file, edit_files_batch, create_file, delete_file |
| 验证 | run_test_structured, run_type_check, run_command, git_diff |
| World | create_object, link_objects, unlink_objects, list_* |

日常改代码路径：`read_*` → `str_replace` → `run_test_structured` / `git_diff`。

## 快速开始

```bash
export DEEPSEEK_API_KEY=...
python3 dp.py
```

## 测试

```bash
python3 -m pytest -q
```

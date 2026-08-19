# Forge

Veritas 上的工程 Agent：精简工具循环 + 事务突变。

## 主路径

```
glob / search / read → str_replace | write_file | apply_patch → test | git_diff
```

磁盘上已有文件**首次写入时自动登记**进 World（ObjectPathMap），无需手动 create_file。

## 工具（LLM 可见约 29 个）

| 类 | 工具 |
|----|------|
| 探索 | glob_files, search_code, find_symbol_definition, read_* |
| 编辑 | **str_replace**, **write_file**, apply_patch, modify_file, … |
| 验证 | run_test_structured, run_type_check, run_command, git_diff |
| 任务 | todo_write, todo_list |
| 网络 | web_fetch |
| World | create_object, link_objects, … |

## 测试

```bash
python3 -m pytest -q
```

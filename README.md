# Forge

Veritas 上的工程 Agent：工具循环 + 事务突变 + Subagent。

## 主路径

```
glob/search/read → str_replace | write_file | apply_patch → test/diff
复杂探索 → spawn_subagent(task) → 只收回结论
```

## 能力摘要

- **自动登记**：磁盘已有文件首次写入自动进入 World
- **Subagent**：独立上下文探索（≤15 步），不污染主对话
- **Todo / WebFetch / apply_patch**
- **会话摘要**：退出时写入 `.forge/session_summary.json`，下次注入 system

## 启动

```bash
export DEEPSEEK_API_KEY=...
python3 dp.py
```

## 测试

```bash
python3 -m pytest -q
```

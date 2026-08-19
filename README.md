# Forge

Veritas 上的工程 Agent：工具循环 + 事务 + Subagent。

## 快速开始

```bash
./install.sh          # 检查 Python / veritasd，跑测试，生成 bin/forge
export PATH="$PWD/bin:$PATH"
export DEEPSEEK_API_KEY=...
forge                 # 或 python3 dp.py
```

## 主路径

```
glob/search/read → str_replace|write_file|apply_patch → (自动 DIFF) → test/diff
复杂探索 → spawn_subagent → 结论
```

## 体验要点

- **改完自动 DIFF**：mutation 成功返回里带 `DIFF:` 块
- **测试失败带源码窗口**：`failure_context`（前后 5 行）
- **启动检查 veritasd**：离线仅警告，文件编辑仍可用
- **会话历史**：`.forge/conversation_history.json`，下次注入摘要
- **自动登记**：磁盘文件首次写入进入 World

## 测试

```bash
python3 -m pytest -q
```

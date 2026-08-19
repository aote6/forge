# Forge

Veritas 工程 Agent：敢改、能撤、大文件不瞎、结果可复制。

## 快速开始

```bash
./install.sh
export DEEPSEEK_API_KEY=...
python3 dp.py
```

## 干活 AI 友好能力

| 能力 | 说明 |
|------|------|
| **undo_last_tx** | 撤销最近一次修改（shadow MVP） |
| **read_file 大纲** | 大文件返回函数/类列表 |
| **BEFORE/AFTER + DIFF** | 改完一眼看懂 |
| **ERROR_SLICES** | run_command 自动抽 Traceback 附近 |
| **project_memory** | 测试命令、最近文件、跨会话 |
| **FORGE 块** | `=== FORGE/... ===` 方便手机复制给其他 AI |

## 测试

```bash
python3 -m pytest -q
```

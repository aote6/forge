# Forge

目标驱动的工程 Agent：接入 AI 后，在工具循环里读代码、搜索、跑命令、规划；  
在 **Veritas 可用** 时，工程写入走事务（Receipt → 投影），而不是只靠直接写盘。

## 不装 Veritas 时实际怎样

| 可用 | 主路径不可用 |
|------|----------------|
| 启动、对话 | `str_replace` / `write_file` 等事务写入 |
| 读文件、搜索、规划 | World 对象 / 链接 / 基于事务的 sync·撤销 |
| 一般 shell、测试等 | （无平行的官方无-Veritas 写盘主路径） |

shell 等仍可能改到磁盘，但不经 Veritas，也没有 Receipt。  
这不是「禁止你改文件」，而是 **当前写入主路径接在 Veritas 上**。

## 装了并启动 veritasd 时多什么

修改经 Intent → 事务 → commit → Receipt → 投影；改错了有世界侧证据可对账，而不只是 git 碰运气。

## 安装

```bash
./install.sh
export DEEPSEEK_API_KEY=...   # 或其它已配置的 AI
python3 dp.py
```

- 需要可用 AI 才能当 Agent 用。  
- **不要求**先装 Veritas 才能安装/启动。  
- 需要走事务写入时，再准备 `veritasd`。

## 使用示例

```text
帮我找一下 str_replace 失败的原因，返回文件和行号
```

```text
把 test_echo.py 里的 hello forge 改成 hello gemini
```

后一类依赖 Veritas 在线才能走通写入主路径。

## 文档

| 文件 | 用途 |
|------|------|
| `docs/FORGE_IDENTITY.md` | 什么算 Forge 身份（防把仓库/平台/Veritas 说成本体） |
| `docs/FORGE_PRODUCT_STANCE.md` | 最小启动路径 |
| `docs/FORGE_VERITAS_STANCE.md` | 有无 Veritas 时能力通断（按实现事实） |
| `docs/FORGE_WORKSPACE_STANCE.md` | 本体 vs 用户工作区 |
| `docs/FORGE_INTERACTION_STANCE.md` | 目标驱动交互 |
| `STATUS.md` | 工程近况 |

这些文档用来对齐「我们在做什么、当前实现边界在哪」，不是功能路线图，也不代替改代码。

## 测试

```bash
python3 -m pytest -q
```

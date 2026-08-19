# Forge 在 Veritas 世界中的角色边界

日期：2026-08-19
状态：更新为工具循环架构

## Forge 是什么

Forge 是 Veritas 世界中的工程 Agent。LLM 通过工具循环操作世界对象和文件系统。

## 三种操作模式

### 1. World 操作（不修改源码）

- create_object: 创建世界对象
- link_objects: 建立对象关系
- list_world_objects / list_world_links: 查询世界状态

这些操作直接经 IntentExecutor -> WorldSession -> Veritas。

### 2. 文件操作（投影到磁盘）

- create_file / modify_file / delete_file

这些操作经 IntentExecutor 创建世界对象后，由 Projection 投影到文件系统。

### 3. 修改 Veritas / Forge 自身

尚未启用的自举场景。当前通过文件操作修改源码后，人工运行测试和重启。

## 边界

- LLM 不直接管理事务（begin/commit/abort 由 IntentExecutor 内部处理）
- LLM 不直接写文件（必须经 Intent -> 事务 -> Projection）
- LLM 不修改 Veritas Kernel（需人工介入）

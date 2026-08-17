# Veritas 自举设计锚点

日期：2026-08-17
状态：设计笔记，非执行计划

## 什么是 Veritas 自举

用 Veritas 修改 Veritas 自身代码，并让修改后的内核接管后续运行。

和编译器自举（用 GCC 编译 GCC）是同一类问题。

## 自举循环

  Forge 修改 Veritas 内核代码
  → 修改通过 Veritas 事务 commit
  → Receipt 产生
  → cargo build 重新编译
  → cargo test 验证
  → veritasd 重启加载新内核
  → Forge 用新内核继续运行

## 自举需要定义的三件事

### 1. 修改边界
- Forge 改内核代码还是只改应用代码？
- 改内核哪些文件需要特权？

### 2. 生效时机
- commit 后立即生效还是重启 veritasd 后生效？
- 立即生效时，正在运行的 Forge 怎么处理旧内核仍存？

### 3. 回滚机制
- 新内核有 bug 时怎么回退？
- 回滚是 Veritas 事务还是 git revert？

## 三步推进计划

第一步：Forge 通过 Veritas 事务修改普通文件（应用代码）
  - 验证主循环闭环
  - 不碰内核

第二步：Forge 修改 Veritas 内核代码，走传统 git 工作流
  - 修改 → cargo test → 通过才 commit
  - veritasd 重启
  - 验证新内核能跑

第三步：Forge 通过 Veritas 事务修改内核，走自举流程
  - 修改 → Veritas commit → Receipt
  - cargo test → 通过才接受
  - veritasd 重启
  - 用新内核验证旧 Receipt 能重放

## 当前状态

**第一步已完成（2026-08-17）**：
Forge 通过 Veritas 事务修改普通文件的闭环已验证。
- P0 Edit Contract Closure 完成（commit `db78bef`）
- 242 tests passed，含真实 veritasd e2e
- ExecutionAdapter → IntentExecutor → WorldSession → veritasd → Receipt → Projection → 文件系统

第二步（传统 git 工作流修改内核）未开始。
自举协议（第三步）不在当前执行范围。
本文件是未来设计锚点，不是今日任务。

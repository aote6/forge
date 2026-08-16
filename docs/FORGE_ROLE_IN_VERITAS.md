
# Forge 在 Veritas 世界中的角色设计

日期：2026-08-16
状态：设计笔记，待实现

## 三种可能的操作模式

### 1. 修改 Veritas 内核代码

Forge 将 Veritas 仓库的文件视为 Veritas Object 进行事务化修改。
每次修改 commit 后 root_hash 变化，产生 Receipt。

自举循环：
  Forge 修改 Veritas 代码
  → Veritas 内核变化
  → veritasd 重启加载新内核
  → Forge 继续使用新内核

这是"用 Veritas 修改 Veritas"，类似编译器自举。
需要明确定义"修改内核 → 重启 → 继续"的流程。

### 2. 通过 Veritas 生成应用

Forge 在 Veritas 世界中创建新 Object，写入应用代码，commit。
这些 Object 就是运行在 Veritas 上的应用。
Forge 是第一个世界软件，它创建的程序是第二个、第三个。

这是 Veritas 的核心用途：世界软件通过 WRI 操作世界。

### 3. 修改 Forge 自身代码

Forge 自身的代码也是 Veritas Object。
Forge 修改自己的代码 = 修改自己的 Object。

自指问题：
  Forge 修改 Forge 代码
  → Forge 行为变化
  → 当前运行的 Forge 仍是旧代码
  → 需要重启 Forge 加载新代码

这是自修改系统，类似 Lisp 修改自己的函数定义。
需要明确"修改何时生效"的语义。

## 当前设计决策

先做最简单闭环：让 Forge 通过 Veritas 事务修改普通文件（应用代码），
并留下 Receipt。

暂不做：
- 修改 Veritas 内核（涉及机器自举）
- 修改 Forge 自身（涉及自修改语义）

## 原因

1. "修改普通文件"能验证主循环闭环
2. "修改 Veritas 内核"复杂度高，需要自举协议
3. "修改 Forge 自身"语义未定义，容易引入自指 bug

先让最简单的事跑通，再逐步增加复杂度。

# Forge

Forge 是运行在 Veritas（确定性世界内核）之上的工程 Agent。

## 架构

LLM（工具循环，边执行边看反馈）
  -> ToolExecutor（32 个工具）
    -> IntentExecutor（事务编排）
      -> WorldSession -> veritasd -> Veritas Kernel
        -> Projection -> 文件系统 / Git / Index

## 核心特征

- 工具循环：LLM 逐步调工具，每步看返回结果再决定下一步（类似 Code Agent）
- Veritas 事务：所有突变走 begin -> execute -> commit/abort，不是直接写文件
- World 操作与文件操作分开：create_object 创建世界对象，create_file 创建文件，不混淆
- Receipt 证据：每次 commit 有 tx_id / version / root hash，可验证

## 快速开始

在 forge 目录下执行 python3 dp.py

输入任务，例如：

创建一个新的 World 对象并 link 到 id=1

正确执行流程：
1. LLM 调 create_object -> 返回 ObjectId=21
2. LLM 调 link_objects(from_id=21, to_id=1, link_type=owns)
3. LLM 文本总结完成

## 测试

python3 -m pytest -q
# 333 passed, 1 xfailed

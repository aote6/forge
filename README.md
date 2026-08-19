# Forge

Forge 是运行在 Veritas（确定性世界内核）之上的工程 Agent。

## 架构

```
LLM（工具循环，边执行边看反馈）
  -> ToolExecutor
    -> IntentExecutor（事务编排）
      -> WorldSession -> veritasd -> Veritas Kernel
        -> Projection -> 文件系统 / Git / Index
```

生产路径唯一：`Runtime.run(task)` → `_run_conversation()` 工具循环。

旧六阶段 Orchestrator / Planner / PlanValidator / engineering 已**删除**，不再存在于仓库。

## 核心特征

- **工具循环**：LLM 逐步调工具，每步看返回再决定下一步（类似 Code Agent）
- **Veritas 事务**：所有突变走 begin → execute → commit/abort，不是直接写文件
- **World 与文件分域**：`create_object` 创建世界对象，`create_file` 创建文件，不混淆
- **Receipt 证据**：每次 commit 有 tx_id / version / root hash，可验证

## 目录

```
adapters/     DeepSeek / Gemini 模型适配
core/         edit_contract、security、patch、file_manager
intents/      Intent 模型与 IntentExecutor
memory/       MemoryStore
projections/  World → 磁盘 / Git / Index
protocols/    共享 dataclass（无 Plan 编排逻辑）
recovery/     ProjectionRecovery（启动重放）
tools/        schema + local_tools + intent_tools（含 create_object）
world/        WorldRuntime / WorldSession / veritasd 适配
runtime.py    唯一生产入口（工具循环）
system_prompt.py  短决策树提示词
```

## 快速开始

```bash
export DEEPSEEK_API_KEY="你的key"
python3 dp.py
```

示例任务：`创建一个新的 World 对象并 link 到 id=1`

正确流程：

1. `create_object` → `ObjectId=<n>`
2. `link_objects(from_id=<n>, to_id=1, link_type=owns)`
3. 文本总结结束

## 测试

```bash
python3 -m pytest -q
```

当前基线：工具循环 + Veritas 核心路径全绿（跳过项依赖 veritasd 环境）。

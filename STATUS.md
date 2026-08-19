# Forge 项目状态

## 定位

Forge 是运行在 Veritas 世界上的工程 Agent。LLM 通过工具循环逐步执行任务，突变经 IntentExecutor → WorldSession → Veritas 事务 → commit → Projection。

依赖方向：Forge → Veritas（经 WorldRuntime → veritasd → Kernel）。

## 生产 Runtime（唯一）

```
User Task
  → Runtime.run(task)
    → _run_conversation(task)   # 唯一执行路径
      → adapter.send(messages, READ_ONLY + MUTATION schemas)
        → ToolExecutor.execute(tool_call)
          → IntentExecutor → WorldSession → Veritas commit/abort
          → ProjectionManager.project(...)
```

- `Runtime.run` 是唯一生产入口。
- `Runtime.run_legacy` 仅交互式只读/确认兜底，已标注 deprecated。
- 突变工具内部处理事务；LLM 不直接管理 begin/commit。
- World 操作与文件操作在工具层硬分开。

## 工具集

### World（不写文件）

- create_object / link_objects / unlink_objects
- list_world_objects / list_world_links / world_info / get_world_object

### 文件（投影到磁盘）

- create_file / modify_file / delete_file

### 只读探索

- list_files / read_file / search_code / get_repo_map / git_* / run_* / …

## 已删除的旧架构（本轮清理）

| 删除项 | 说明 |
|--------|------|
| `forge/orchestrator/` | 六阶段 EngineeringOrchestrator |
| `forge/planner.py` / `plan_validator.py` | 旧 Plan 生成与校验 |
| `forge/engineering/` | 旧工程状态机 |
| `forge/context/` | Planner 用仓库索引/快照 |
| `forge/adapters/execution.py` 等 | 仅服务 Orchestrator 的适配器 |
| `forge/protocols/operation_contract.py` / `world_operations.py` | Plan 侧 SSOT |
| `forge/failures.py` / `forge/verification/` | 旧 VERIFY 路径 |
| `forge/memory/checkpoint.py` | Orchestrator TaskCheckpoint 存储 |
| 一批 `tests/test_planner*` / `test_orchestrator*` / `test_plan_*` 等 | 验证旧路径的测试 |

## 保留核心

- `runtime` / `tools` / `world` / `intents` / `projections` / `recovery`
- `core`（edit_contract 等）
- `adapters`：base / deepseek / gemini
- `protocols/models.py`：共享 dataclass（无编排）

## 已知限制

- veritasd 需在 PATH 或默认路径可用
- 单活跃 Session（Runtime 级）
- 工具循环默认最多 20 轮

## 测试状态

- 本轮清理后：全绿（passed + skipped 依赖 veritasd；无 failed）
- `tests/test_tool_loop_create_object_wiring.py`：create_object 工具接线

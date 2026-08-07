# Forge 项目状态

## 定位

Forge 是运行在 Veritas 世界上的 Engineering Orchestrator（唯一工程编排入口）。

依赖方向：Forge → Veritas（经 WorldRuntime → veritasd → Kernel）。

## 生产 Runtime（唯一）

```
User Task
  → Runtime.run(task)
    → EngineeringOrchestrator.run()
      → UNDERSTAND (Hub → zhiwang)
      → PLAN       (Planner + PlanValidator)
      → CHECK      (Hub → lu)
      → EXECUTE    (ExecutionAdapter → IntentExecutor → Veritas → Projection)
      → VERIFY     (Hub → sms)
      → COMPLETE / FAILED  (TaskCheckpoint)
```

- `Runtime.run` 是唯一生产工程入口。
- `Runtime.run_legacy` 仅保留为交互式只读工具循环，禁止用于工程变更。
- Hub 失败即 Task Failed（无本地 fallback）。
- modify / delete 必须解析到 world object_id，禁止静默 create。
- VERIFY 失败重新进入 UNDERSTAND → PLAN（最多 MAX_SELF_CORRECTION 次）。

## 已知限制

- veritasd 需在 PATH 或默认路径可用
- 单活跃 Session（Runtime 级）
- Hub（zhiwang / lu / sms）为生产强制依赖
- 大仓库内容加载受字符预算限制；文件树列表始终完整

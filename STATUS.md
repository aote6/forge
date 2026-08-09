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

## 2026-08-09 Planner 全量扫描问题（已止血，未根治）

- **症状**：`[Planner DEBUG] user prompt len=32644`，Planner 在构造 prompt 阶段无条件调用 `repo.file_tree` 全量快照，无论任务是否需要。
- **根因**：调用链上缺少"任务相关性筛选"层——zhiwang 无脑给全量，repo_adapter 无脑解析全量，Planner 无脑塞全量进 prompt。
- **止血**：zhiwang 06_file_tree.sh 新增 `.forge` 排除规则（886→124），降低 prompt 膨胀。
- **技术债务**：真正需要的是 Planner 层根据任务类型筛选相关文件子集，而非依赖快照层的排除规则。目前筛选逻辑从未落地过。
- **相关 commit**：`11a44ee` 的 message 暗示修过但实际代码证明未触及核心全量扫描逻辑。

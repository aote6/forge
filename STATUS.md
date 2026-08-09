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

## 2026-08-09 dp.py 意图路由缺失 + modify 链路三处静默失败（已根治）

**触发场景**：`dp` 交互中输入只读查询（"查看一下xxx结构"）被无条件塞入六 Phase
EngineeringOrchestrator，生成了完整的修改计划而非直接回答。追查过程中顺带
发现 modify_file 全链路存在三处独立的"静默假成功"问题。

**发现1：dp.py 无意图分流**
- 症状：所有输入不分只读/修改，一律走 `Runtime.run` → EngineeringOrchestrator。
- 修复：`dp.py` 新增 `is_engineering_task()` 关键词分类器。只读查询走
  `Runtime.run_legacy`（LLM 自主工具调用循环：list_dir/read_file/grep，
  这条路径此前完整存在但从未被入口调用过）；修改类任务照常走
  `Runtime.run` → EngineeringOrchestrator。
- 遗留：关键词表是最小起步方案，后续误分类需持续补词，未来可考虑升级为
  LLM 意图判断。

**发现2：FileProjection._dicts_to_edits 静默吞掉格式错误的 operation**
- 症状：`test_modify_existing_file` 测试通过（无异常），但文件内容未变。
- 根因：operation dict 缺少 `start_line`/`end_line`/`new_lines` 字段时，
  旧代码用 `.get(key, 0)` 默认值兜底，生成一个 `start_line=0, end_line=0,
  new_lines=[]` 的空 edit，`apply_edits` 对此的行为是原样返回原文件——
  等同于什么都没改，但外层仍返回 `success=True`。
- 修复：`_dicts_to_edits` 改为显式校验，缺少必需字段时 `raise ValueError`，
  错误会冒泡到 `apply()` 的 except 分支，返回 `success=False`，不再假成功。

**发现3：Veritas kernel tx_write 不支持指定 object_id（架构级缺口）**
- 症状：`_modify_file_in_session` 正确传入 `object_id`，但写入的内容从未
  出现在目标对象上。
- 根因：`Session.write(object_id, ...)` 从未把 `object_id` 传给底层
  `tx_write`（对比 `freeze`/`death`/`link` 均显式传 object_id）。Rust 端
  `WorldService::tx_write` 签名里根本没有 object_id 参数，写入目标完全
  依赖 session 内部的 `current_object` 游标（仅在 `create_object` 时设置）。
  Modify 场景的 session 从不调用 create_object，`current_object` 始终未
  正确指向目标对象，写入实际落空。
- 修复（4 层，两个仓库）：
  - `veritas_kernel/src/world_api.rs`：`tx_write` 新增
    `object_id: Option<ObjectId>` 参数，若与 current_object 不同则先
    `enter_object`（复用 tx_freeze_object/tx_death_object 已有模式）。
  - `veritas_kernel/src/bin/veritasd.rs`：JSON handler 解析可选
    `object_id` 字段并透传。
  - `forge/forge/world/adapter.py`：`tx_write` 增加 `object_id` 参数，
    非空时写入请求体。
  - `forge/forge/world/session.py`：`write()` 把入参 `object_id` 真正
    传给 adapter（此前是死参数，从未使用）。
  - veritas_kernel commit: `4e7403a`

**发现4：ProjectionManager 默认 checkpoint 目录硬编码为生产 `.forge/`**
- 症状：全新临时目录 + 全新 World 实例的测试，`receipt.version` 从 1/2/3
  开始，但 modify 阶段的 projection 被判定为 `skipped: version <= checkpoint`
  而完全跳过写入。
- 根因：`ProjectionManager()` 默认 `ProjectionCheckpoint(store_dir=".forge")`，
  这是相对当前工作目录的路径，测试运行时实际读写的是
  `~/forge/.forge/projection_checkpoint.json`——生产环境持续累积的 version
  （当时为 8），远大于测试临时 World 的 version（2-3），导致新版本被误判
  为"已处理过的旧版本"而跳过。
- 修复：`ProjectionManager.__init__` 新增可选 `checkpoint_dir` 参数。
  8 处测试改为传入各自临时目录下的 `.forge`，与生产 checkpoint 完全隔离。
  `test_e2e_veritas_forge.py`、`test_recovery.py` 中的用法是刻意共享
  checkpoint 以测试幂等/恢复语义，未改动。

**教训**：这四处 bug 全部是"多 AI 接力协作在接口边界留下隐式假设，没有
运行时校验、没有端到端验证"的直接产物——每一处单独看都不复杂，但分散在
不同文件、不同仓库（Forge + Veritas kernel + zhiwang），任何一次孤立的
代码审查都很难发现，只有真正跑一次端到端 modify 操作才能暴露。以后新增
跨模块协议（operation 格式、object_id 传递、checkpoint 作用域）时，应
优先加运行时校验（fail loud）而非依赖约定；测试必须使用与生产环境隔离
的状态存储。

**验证**：全量 `pytest` 208 passed。相关 commit：forge `ac43fb5`，
veritas_kernel `4e7403a`，zhiwang（06_file_tree.sh，独立仓库，未记录 hash）。

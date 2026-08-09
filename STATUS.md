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

## 2026-08-09 forge 接住 tx_commit 的 AdminCap capability_grants(已根治+持久化)

**背景**：内核侧(veritas_kernel)已修复 `create_object_short` 不再丢弃 AdminCap，
`TransactionDeltaView` 新增结构化 `capability_grants` 字段。forge 端需要接住这把钥匙。

**发现1：forge 真实调用链路走的是 `tx_create_object`（事务内建对象），不是
独立测试用的 `create_object_short` 路由**
- 排查中发现 `forge/adapters/veritas_adapter.py` 的 `VeritasAdapter` 类是死代码：
  顶部 `from forge.protocols.models import TransactionRequest, TransactionReceipt`
  导入的两个类在整个项目里都不存在，import 时就会崩溃，且全项目无任何调用点。
  真实执行路径是 `ExecutionAdapter.execute_proposal()` → `IntentExecutor.execute_batch()`。
  `veritas_adapter.py` 未删除，留作后续清理项。

**发现2：Python 侧 `TransactionDelta` 缺少 `capability_grants` 字段，
`receipt_parser.py` 只解析旧的 `capability_events`（字符串日志），
结构化 cap 数据在 JSON→dataclass 这一跳被直接丢弃**
- 修复：`forge/world/types.py` 新增 `CapabilityGrantView` dataclass + 
  `TransactionDelta.capability_grants` 字段；`receipt_parser.py` 补上对应解析。

**发现3：当次事务内可用 vs 跨进程重启可查，是两个不同时间尺度的需求**
- `IntentExecutor.execute_batch()` 提交后从 `delta.capability_grants` 按
  self-admin-cap 模式（`grantee == resource`）提取 cap，写入
  `delta.metadata["capability_map"]`，供同一 batch 内后续操作立即使用。
- 持久化问题：宪法要求 capability_id 是稳定身份，不得在 restore 时重新生成，
  且内核测试（`kernel_world_persists_across_sequential_machines` 等）已表明
  对象本就设计为跨进程重启存活。若 cap 只在单次事务内可用，进程重启后对
  同一对象做操作（如 freeze）将因新加的越权校验（见下方"CRITICAL 安全修复"
  记录，位于交接摘要）而被拒绝。
- 修复：`ObjectPathMap`（`forge/projections/object_path.py`）新增 `_caps` 
  字典与 `get_capability_id(object_id)` 方法，`update_from_delta()` 顺手从
  `delta.capability_grants` 提取 self-admin-cap。因 `WorldRuntime._rebuild_path_map()`
  已经通过重放 `receipts_since(0)` 在启动时重建整个 path map，cap map 免费
  获得同样的跨进程重启存活能力，未新开持久化文件。

**验证**：全量 `pytest` 207 passed，1 deselected（见下方独立 bug）。
相关 commit：forge `ccecc79`（capability_grants 接入）、`665950e`（cap 持久化）。

## 2026-08-09 test_e2e_veritas_forge.py 绝对路径处理 bug（新发现，未修复）

**症状**：`test_e2e` 中 `test_path` 为绝对路径
（`/data/data/com.termux/files/home/forge_e2e_test.py`），
`FileProjection.apply()` 执行后该路径下文件未被创建，`os.path.exists(test_path)`
断言失败。副作用：会在当前工作目录（`~/forge`）下生成一个把整条绝对路径
十六进制编码后当作文件名的垃圾文件（两次意外被 `git add -A` 一并提交，
已在 commit `060e3d7` 清理，但只要重跑该测试就会再生成）。
- **确认**：与今天 capability_grants 相关改动无关，在改动前的 `060e3d7` 版本
  同样复现，是预先存在的独立 bug。
- **疑似根因方向**：`FileProjection.apply()` 或路径解析逻辑在处理绝对路径
  （而非相对 project_root 的路径）时拼接方向有误，把整条路径误当 basename
  或做了不该做的编码。尚未定位具体代码行，需要下个窗口单独排查。
- **当前状态**：`pytest` 需 `--deselect tests/test_e2e_veritas_forge.py::test_e2e`
  才能全绿；未 deselect 直接跑会污染 cwd，产生垃圾文件。

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

## 2026-08-13 CapabilityGrant P2 薄适配完成

### 背景

Veritas 侧已完成：
- P0：CapabilityGrant grantor 语义闭环
- P1：veritasd 暴露 tx_capability_grant JSONL 命令

Forge 侧需要做的是薄适配，不复制授权语义，只做 JSONL 转发。

### 完成内容

- `forge/world/adapter.py`：新增 `tx_capability_grant(session_id, grantor, grantee, capability_type, resource)` 方法
  - 发送 cmd=tx_capability_grant，复用 _require_ok 错误处理
  - 无本地授权逻辑，纯转发
- `forge/world/session.py`：新增 `grant(grantor, grantee, capability_type, resource)` 薄包装
  - 只做 _ensure_open() + adapter 转发
  - 与现有 write/link/freeze 风格一致
- `tests/test_p2_capability_grant.py`：2 个 e2e 测试
  - test_p2_capability_grant_a_grants_b_on_c：完整验证未授权失败 → grant → 授权成功
  - test_p2_adapter_tx_capability_grant_direct：adapter 层直接调用验证

### 验证

- 全量 pytest 210 passed（含 P2 新增 2 个测试）
- 调用链：Forge WorldSession.grant → WorldAdapter.tx_capability_grant → veritasd JSONL → WorldService::tx_capability_grant → KernelCall::CapabilityGrant → Engine/CapabilityGraph

### 边界

- 未修改 Veritas 任何代码
- 未修改 CapabilityGraph
- 未在 Forge 增加本地授权缓存/判断
- 未绕过 JSONL，未构造 KernelCall

### 相关 commit

- forge: 3228070 feat: Forge CapabilityGrant 薄适配
- forge: 9d6d474 fix: P5 修改语义 0-indexed 半开区间 + FileProjection 绝对路径处理
- veritas_kernel: c01b0ed feat: expose CapabilityGrant through veritasd

---

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


## Identity Binding Boundary

- Forge 将 World ObjectId 持久化到 .forge/world_identity
- attach_identity(object_id) 当前验证 Object 存活，但不验证外部调用主体是否有权声明该 Object 为自身身份
- 当前 veritasd 为本地 stdin/stdout 进程，部署模型为单用户本地信任边界
- 因此当前定级为 MINOR / KNOWN DESIGN GAP，而非 Kernel security bug
- 若未来引入多用户、远程客户端或网络 veritasd，必须在 WRI 层增加 authenticated identity binding，并重新审计 attach_identity 全链路
- 详细审计：docs/IDENTITY_BINDING_AUDIT.md

## 2026-08-17 P0: Forge Edit Contract Closure（已冻结）

**背景**：跨仓库架构审计发现 Forge 的 modify_file 链路存在双轨制：Planner/PlanValidator 使用 1-based inclusive + `new_text`，而 IntentExecutor/FileProjection/PatchEngine 要求 0-based half-open + `new_lines`。全链路无转换边界，导致 commit 成功后 Projection 可能静默跳过写入（`new_lines` 缺失默认 `[]`），形成"假成功"。

**修复**：
- 新增 `forge/core/edit_contract.py`：冻结双 schema 契约
  - Authoring Edit（Planner/LLM/人类）：1-based inclusive + `new_text`
  - Machine EditOp（Intent/Veritas/Projection/PatchEngine）：0-based half-open + `new_lines`
  - 唯一转换函数：`authoring_to_machine_ops()`（`start0 = start_line - 1`，`end0 = end_line`，`new_lines = splitlines(keepends=True)`）
- `ExecutionAdapter.execute_proposal`：成为生产路径唯一转换边界，调用 `proposal_ops_to_machine()`
- `IntentExecutor._validate_intent`：只接受 Machine EditOp，用 `validate_machine_op()` 拒绝 `new_text`/`old_text`
- `FileProjection._dicts_to_edits`：只接受 Machine EditOp，`new_lines` 缺失不再静默 `[]`，改为报错
- `PatchEngine`：完全无 `old_text`/`new_text` 残留
- `ExecutionResult.status` 新增：`COMPLETE` / `ABORTED` / `WORLD_COMMITTED_PROJECTION_FAILED` / `FAILED`
  - 区分"事务中止"和"世界已提交但投影失败"，不再混淆
- `WorldAdapter.world_info` / `receipt_parser`：`_as_root()` 统一解析十进制/十六进制 root

**验证**：
- 新增 27 个单元测试（`test_edit_contract.py`）：转换 golden、trailing newline、machine-only 拒绝、status 语义
- 新增 5 个 e2e 测试（`test_edit_contract_e2e.py`）：精确字节、abort 不写盘、projection failure status、**真实 veritasd commit→project→bytes**
- 全量 `pytest`：**242 passed, 0 failed**（含真实 veritasd e2e）
- `git diff --check`：通过

**契约状态**：FORGE EDIT CONTRACT = CLOSED

**遗留（非 P0 范围）**：
- World commit 与 FS apply 仍非原子（已冻结为 `WORLD_COMMITTED_PROJECTION_FAILED`，不冒充 COMPLETE）
- `test_e2e_veritas_forge` 绝对路径历史问题未处理
- veritasd 未在 PATH，真实 e2e 依赖 `~/veritas_kernel/target/release/veritasd`

**相关 commit**：forge `db78bef`

## 2026-08-17 待解决：veritasd state_root 输出格式与内核 SHA-256 迁移不同步

**问题**：Veritas 内核已迁移到 SHA-256（`engine.rs` 的 `state_root()` 返回 `[u8; 32]`），但 veritasd 的 `world_info` JSON 输出仍然是旧的 u64 十进制整数。

**现状**：
- Veritas 内核：`state_root()` → `[u8; 32]`（SHA-256）
- veritasd JSON：`"state_root": 17833039905061984046`（u64 十进制，FNV-1a 旧值）
- Forge `WorldAdapter.world_info`：`int(resp.get("state_root"))`（假设十进制）
- Forge `receipt_parser._as_root`：十进制优先，十六进制兜底

**影响**：
- Forge 当前依赖 veritasd 的旧输出格式
- 当 veritasd 同步到 SHA-256 后，输出变成 64 字符 hex 字符串，Forge 的 `int()` 会直接崩溃
- 这是 Veritas ↔ Forge 接口契约未冻结的典型案例

**正确方向**：
- WRI 需要冻结 `state_root` 输出格式：32 字节 SHA-256 hex 字符串（64 个 hex 字符）
- veritasd 的 `world_info` 应该输出 `state_commitment` + `commitment_algorithm`
- Forge 只需要按 WRI 约定解析，不关心 Veritas 内部用什么哈希算法
- Veritas 未来从 SHA-256 换到 SHA-512，Forge 无需改代码

**状态**：未解决，等待 WRI v1.0 冻结

## 2026-08-17 CREATE_OBJECT 语义穿透 + capability_grants 序列化修复

背景：昨晚任务要求用 tx_create_object 创建对象并查看 object_count，但 Planner 卡在 planning 阶段，Intent 层没有纯 CREATE_OBJECT 语义。同时审计发现 veritasd receipt_json 未序列化 capability_grants，Forge 的 AdminCap 接入在真实路径上断裂。

修复：
- forge/intents/intent.py：新增 IntentType.CREATE_OBJECT + Intent.create_object() 工厂方法
- forge/intents/executor.py：新增 _create_object_in_session handler，纯 ObjectBirth 无 path/content/write
- veritas/src/bin/veritasd.rs：receipt_json 补上 capability_grants 结构化序列化

完整链路：
Intent.create_object()
  到 IntentType.CREATE_OBJECT
  到 IntentExecutor._create_object_in_session
  到 WorldSession.create_object()
  到 WorldAdapter.tx_create_object
  到 veritasd tx_create_object
  到 Kernel ObjectBirth
  到 commit
  到 Receipt.delta.objects_created + capability_grants
  到 intent.parameters[_created_object_id]

验证：
- Forge 单元测试：5 passed
- Forge e2e：4 passed，真实 veritasd commit abort capability_grants 无文件侧效应
- Forge 全量：251 passed
- Veritas 全量：360 passed, 0 failed
- 审计工具：Verification Map 245/245 PASS，Instruction Dispatch 28/30，2 MISSING 历史遗留

契约状态：CREATE_OBJECT 底层闭合，capability_grants 跨边界闭合

遗留：
- Planner / PlanValidator 尚不支持 create_object operation_type
- 同 batch 自动把新 object_id 交给后续 Intent 无变量机制
- DELETE_OBJECT 仍在 enum 无 handler

相关 commit：forge 待提交，veritas 待提交

## 2026-08-17 Planner 支持 create_object operation_type

背景：CREATE_OBJECT 底层已闭合，但 Planner 还不会产生 create_object。用户说创建对象时 Planner 卡住。

修复：
- planner.py：operation_type 枚举新增 create_object，规则明确 target_files 必须为空数组，不需要 content start_line end_line new_text
- plan_validator.py：valid_ops 新增 create_object，接受空 target_files，拒绝非空 target_files
- adapters/execution.py：在 target_files 检查之前处理 create_object，生成 Intent.create_object()

完整链路：
用户任务
到 Planner operation_type=create_object target_files=[]
到 PlanValidator 允许空 target_files
到 ChangeProposal type=create_object
到 ExecutionAdapter Intent.create_object()
到 IntentExecutor IntentType.CREATE_OBJECT
到 WorldSession.create_object()
到 veritasd tx_create_object
到 Kernel ObjectBirth
到 Receipt objects_created + capability_grants

验证：
- 单元验证：create_object 空 target_files 通过，非空 target_files 拒绝
- 全量 pytest：251 passed
- git diff --check：通过

契约状态：CREATE_OBJECT 全链路闭合，从 Planner 到 Veritas ObjectBirth

相关 commit：forge c1da209

## 2026-08-17 机器与 LLM 决策权边界正式沉淀

背景：今天发现 Planner 把创建对象误判为修改代码。追查历史发现这是第三次踩同一条线：
- 8月6日 PlanRepair 替 AI 猜 old_text，删除
- 8月9日 dp.py 无读写分流，机器粗分类修复
- 8月17日 Planner 无语义纠偏，Validator 需要 fail-closed

正式原则：
机器可以做入口粗分类、客观事实提取、能力存在性标注、契约裁决。
机器不得事后纠正 LLM 的 operation_type、自动补全缺失字段、猜测 LLM 意图。
Validator 只拒绝，不修正。

新增文档：
- docs/PLANNER_DECISION_BOUNDARY.md：完整决策权边界
- docs/RECOVERY_CONSTITUTION.md 附录第 7 条：原则正式入宪

状态：原则已冻结，实现待补
下一步：按决策权边界实现 Planner 两阶段决策和 Validator fail-closed

## 2026-08-17 Planner P0：Repository Facts + validation retry（无 semantic repair）

背景：决策权边界原则已冻结，但 Planner 缺少机器事实注入和 Plan 级校验重试。本次 P0 验证核心假设——"给 LLM 提供足够仓库事实后，它能否自行正确选择 operation_type"。

实现：
- forge/context/planning.py：新增 compute_repository_facts(index, task_symbols)，只输出 DEFINED/NOT_DEFINED + definition location，不做 capability 判断
- forge/planner.py：Repository Facts 注入 user_prompt；MAX_VALIDATION_RETRIES=2；REJECT 后重试 prompt 包含原始任务 + facts + 上一轮完整 Plan JSON + rejection reason
- forge/plan_validator.py：fail-closed 硬校验强化，明确禁止自动改写 operation_type / target_files
- tests/test_planner_p0_facts_retry.py：新增 10 个 P0 测试

数据流：
task_symbols（extract_focus_symbols）
→ RepositoryIndex.find_definition
→ Repository Facts（DEFINED/NOT_DEFINED + location）
→ LLM 决策 operation_type
→ PlanValidator ACCEPT / REJECT
→ REJECT 后带完整上下文 retry（max 2）
→ 耗尽 fail-closed

验证：
- P0 测试：10 passed
- 全量 pytest：263 passed, 1 xfailed
- 反模式测试 test_planner_must_correct_llm_modify_to_create_object 标记 strict xfail，作为架构护栏记录"禁止 semantic repair"

契约状态：机器提供事实 → LLM 决策 → Validator 只拒绝不修正，闭环跑通。

遗留：
- 尚未做真实 LLM 实验验证"Facts 注入后 LLM 是否不再误判 modify"
- P1 WORLD_OPERATION/CODE_OPERATION 二阶段 gate 暂不引入，等真实失败样本

相关 commit：forge 3d16e6f

## 2026-08-17 P7：create_object 豁免 mutation-obligations

背景：P0-P2 后，纯 create_object plan 被 compute_obligations 错误要求覆盖源码 definition/caller 文件，导致"创建 World 对象"任务被 Validator 拒绝。

修复：
- is_runtime_only_plan：全部步骤均为 create_object 时返回 True
- plan_mutation_files：create_object 不计入 mutation files
- missing_required_obligations：纯 runtime 计划跳过 coverage
- 混合计划（create_object + modify）仍 fail-closed

测试：+3（纯 create_object 通过 / modify 仍拒 / 混合仍拒）
全量：297 passed, 1 xfailed
相关 commit：forge 7e05a16

## 2026-08-17 P8：Constitution runtime/mutation 边界

背景：P7 后，纯 create_object 通过 Validator 但在 CHECKING 被 constitution.check 的 forge.content_required 拒绝——Constitution 把所有 ChangeProposal 当源码修改，要求必须带 content。

修复：
- 纯 create_object proposal（所有 op 均为 create_object）跳过 content_required，直接 PASS
- modify/create_file/delete_file 仍受 content_required 约束
- 混合 plan 不受豁免

测试：+4（纯 create_object 通过 / modify 仍拒 / 混合仍拒）
全量：301 passed, 1 xfailed
相关 commit：forge 40110d3

## 2026-08-17 端到端实验：create_object 全链路闭合

实验：真实 DeepSeek adapter + 真实 Veritas 执行

任务：创建一个新的 World 对象，调用 tx_create_object，不修改任何源码文件。

结果：
- Planner：LLM 正确选择 create_object（assumption 明确"纯运行时计划"）
- PlanValidator：通过
- P7 obligations：纯 runtime 豁免生效
- P8 Constitution：runtime 豁免生效
- ExecutionAdapter：Intent.create_object() 成功
- Veritas：3 次 TXCOMMIT（TX=3,4,5）
- VERIFY：pass，failures=[]
- 任务 checkpoint：phase=completed，errors=[]
- World 实际状态：version=5，object_count=5，5 个 Alive 对象

全链路证据：
Planner → create_object → Validator ACCEPT → Constitution PASS → ExecutionAdapter → Intent → Veritas TXCOMMIT → WAL → World version 递增 → VERIFY PASS

契约状态：runtime operation 与 mutation operation 边界在 Planner/Validator/obligations/Constitution/ExecutionAdapter 五层全部闭合。

遗留（下一层，不与 P8 混修）：
- 用户说"创建一个对象"，Planner 生成了 3 个 create_object step——需要审查 Planner 对任务数量约束的处理

## 遗留：RepositoryIndex / Snapshot 全量重扫（性能）

问题：dp / gg 每次输入任务都会触发全量仓库扫描 + 全量 AST 索引重建。
- take_snapshot：每次 os.walk + tree_hash
- RepositoryIndex.build：每次 scan_files + 逐个 ast.parse
- 项目变大后 token 和延迟都不可接受

根因：无增量缓存，相同 snapshot_id 不复用索引。

修复方向（明天）：
- take_snapshot 按 tree_hash 缓存
- RepositoryIndex.build 相同 snapshot_id 直接返回已有索引
- scan_files 增量检测，只重扫变更文件

状态：未开始，不与 P0-P8 边界修复混修。

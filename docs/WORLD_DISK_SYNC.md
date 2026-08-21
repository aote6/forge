# World ↔ Disk/Git Synchronization Contract

**Status:** Architecture Contract — Frozen before implementation

**Scope:** Forge World / Veritas / FileProjection / GitProjection / Recovery

This document defines the synchronization boundary between the persistent
Veritas World and the external project workspace. Implementation MUST conform
to this contract.

---

## 1. Veritas World 是历史事实记录器，不负责同步磁盘

Veritas World 是 Forge 的持久世界状态与历史事实记录器。

World / Veritas 负责记录：

- transaction
- object / link / capability state
- commit version
- WAL
- receipt / delta
- other persistent World state

Receipt 表示：

> World 在某个版本发生了什么事实。

Receipt 不是磁盘恢复指令。

因此：

- Veritas 不负责决定磁盘应该是什么样
- Veritas 不主动把 receipt 重放到磁盘
- Recovery 不得把 World receipt 当作覆盖磁盘的授权
- Git / Disk 同步不属于 Veritas Kernel 的职责

Veritas 的职责到 World state / historical fact 为止。

---

## 2. Disk + Git 是文件内容的权威存储

对于 Forge 管理的项目文件：

> Disk working tree + Git history 是文件内容的实际存储。

World 不缓存文件内容副本。

这里的"不缓存文件内容"特指：

- World 不维护一份独立的项目文件副本
- World 不把 receipt 当作文件内容备份
- World 不通过自己的文件副本覆盖工作区

World 可以保存同步所需的元数据 / 指针 / 已知状态，例如：

- last_known_commit
- file hashes
- synchronization checkpoint
- projection consumption state

这些属于同步索引，不属于文件内容副本。

---

## 3. Sync 是 Forge 层的职责

Forge Sync 负责比较：

- World 已知状态
- Disk 当前状态
- Git 历史状态

并产生明确状态：

### IN_SYNC

World 与 Disk/Git 已经处于同一已知状态。无需同步。

### FAST_FORWARD(direction)

只有一侧发生了新的、另一侧尚未记录的变化。允许沿明确方向推进同步。方向必须明确，不能由 recovery 隐式决定。

### CONFLICT

双方在共同已知状态之后都发生了独立变化。此时不能自动选择任意一方覆盖另一方。必须停止自动同步、报告冲突、提供 diff 信息、等待用户明确决定如何处理。

判断同步状态时，必须同时考虑：

- Git commit ancestry
- working tree / 文件 hash
- World sync metadata

不能只依赖其中任意一项。

文件级 hash 不应被直接等同于最终语义冲突。在可能的情况下，可以进一步使用 Git 三路合并 / diff 判断是否能够安全合并。

---

## 4. 分叉必须 STOP，不得伪造同步成功

任何发现 World 与 Disk/Git 已经发生未解决分叉的流程：

**MUST STOP**

不得：

- 自动覆盖磁盘
- 自动覆盖 World 状态
- 把分叉文件简单 skip 后继续
- 因为 skip 而推进 projection checkpoint
- 把未同步状态标记为同步成功

特别禁止以下模式：

第一步：detect divergence（检测到分叉）
第二步：skip file（跳过文件）
第三步：projection success（投影标记为成功）
第四步：checkpoint advance（推进 checkpoint）

因为这会造成：

> checkpoint 声称已经消费 / 同步，实际文件却没有同步。

正确语义必须是：

第一步：detect divergence（检测到分叉）
第二步：STOP / CONFLICT（停止并标记冲突）
第三步：explicit resolution（用户明确决定）
第四步：successful synchronization（真正同步成功）
第五步：advance checkpoint（推进 checkpoint）

Checkpoint 表示：

> 对应 projection 已经实际完成同步。

它不是"我看过这个 receipt"的计数器。

---

## 5. Sync metadata 必须持久化

至少包括：

- last_known_commit
- last_known_file_hashes
- projection checkpoints
- 必要的 sync transaction metadata

存储可以是：

- .forge/sync_state.json
- 或者 Veritas World State 的专门 projection

具体落点进入审计时再决定，不提前绑定实现。

---

## 6. Receipt 是事实，不是恢复指令

Receipt 描述：

> 某个世界事务曾经发生。

不是：

> 下次启动时必须把磁盘改成这个样子。

Receipt 应能区分来源，例如：

- source = forge_tool
- source = external_sync

external_sync 表示：

> 用户进行了外部同步这一事实

而不是伪造一笔"AI 修改了文件"的 World transaction。

---

## 7. Sync 必须覆盖运行时和显式同步

### forge sync 行为

1. 检查当前状态
2. IN_SYNC 则什么都不做
3. FAST_FORWARD 则执行对应方向的安全同步
4. CONFLICT 则 STOP 并展示 diff 等待决定
5. 成功后更新 sync metadata
6. 记录 external_sync 等同步事实

### Forge/DP 正在运行时

持有 sync/write lock 期间，发现外部磁盘变化，立即停止当前写操作/任务，进入重新对账。

---

## Architectural Boundary

顶层是 Forge / DP，包含 Planner / Executor / WorldRuntime。

中间是 Veritas，包含 World State / Transaction / WAL / Receipt / History / Commitment。Veritas 不负责磁盘同步。

再往下是 Forge Sync Layer，负责 state detection / fast-forward / conflict detection / explicit sync / checkpoint coordination。

最底层是 Disk + Git，是实际项目文件内容，包含 working tree / Git history。

---

## Non-Goals

本契约不要求：

- 重写 Veritas Kernel
- 修改 Veritas transaction semantics
- 让 Veritas 理解 Git
- 让 Git 成为 Veritas World 的替代品
- 自动解决所有文件 merge conflict

本阶段只解决：

World 与外部工作区之间的双向同步边界和一致性语义。

---

## Implementation Rule

在本契约冻结后：

1. 先审计现有 Projection / Recovery / Checkpoint / Git / Sync 代码
2. 标记所有违反本契约的路径
3. 再逐项实现修改

新测试必须验证本契约，而不是为现有实现找理由。

不允许通过修改测试来适应违反本契约的旧行为。

---

## Implementation notes (SyncLayer)

### External untracked detection (gap #1)

`detect()` treats git `??` paths that are **not** in the Forge-known path set as
`CONFLICT` with `conflict_kind=untracked_external`.

Forge-known paths = `last_known_file_hashes` keys ∪ paths extracted from
World receipt history (`memory_written` state_id=0).

**Performance (known follow-up):** each `detect()` currently full-scans receipt
history via `get_receipts_since(0)` to build the known path set. If receipt volume
makes `detect()` slow, add an in-process path cache or incremental index. This is
**out of scope** for the gap #1/#4b fix and is not a silent correctness tradeoff.

### Mid-batch write guard (gap #4b)

`FileProjection.apply` snapshots hashes of existing targets before the batch,
re-checks each path immediately before writing it, and on drift stops the batch,
rolls back already-written files via `BackupManager`, and never advances
`disk_synced_version`. Paths whose rollback fails are reported as
`uncertain_paths` and removed from `last_known_file_hashes`.

Single-file writes use atomic temp-file + `os.replace` in `FileManager.write`.

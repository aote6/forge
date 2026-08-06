# Recovery Constitution

## Scope

本宪法定义 Forge Projection Recovery 的架构不变量。
它描述 Projection 的恢复协议、Checkpoint 语义、Recovery Watermark、
幂等边界及恢复正确性约束。

本文件属于 Forge，自成体系，与 `world_runtime_interface.md` 互补：
- `world_runtime_interface.md` 定义 Forge 与 Veritas 的接口（WRI）。
- `RECOVERY_CONSTITUTION.md` 定义 Forge Projection Recovery 的恢复语义与不变量。

Recovery Constitution 不定义 Veritas Transaction、Receipt 生成或 World 事务模型；
这些由 Veritas Constitution 负责。本文件仅建立在 WRI 所保证的接口契约之上。

## 前提

以下前提由 Veritas Kernel 保证：

1. Receipt.version 在 commit 成功时分配，全局唯一，严格递增，永不复用
2. 一次 commit 对应一个 version，对应一个 receipt
3. receipts_since(v) 返回所有 version > v 的已提交 receipt，按 version 升序排列

若上述前提不成立，本文档规定的协议不适用。

## Recovery 不变量

### 1. Receipt 按 version 升序处理

Recovery 必须按 version 从小到大依次处理 receipt。
严禁跳过或乱序处理。

### 2. Checkpoint.version 表示连续成功消费到的最高 version

version = N 意味着：version ≤ N 的所有 receipt 都已成功投影。
不存在"中间某个 version 失败但 version 已推进"的状态。

### 3. apply 失败不得推进 version

若某 receipt 的 apply 失败，version 不得推进。
下次 Recovery 必须重新尝试同一 receipt。
严禁失败后跳过该 receipt 继续推进 version。

### 4. Recovery 不允许跳号推进

version 只能从 N 推进到 N+1。
严禁从 N 跳到 N+K（K > 1）——即使 receipts_since 返回了多个 receipt，
也必须逐个 apply、逐个推进。

### 5. Checkpoint 是唯一持久 Recovery Watermark

ProjectionCheckpoint 只保存 {projection_name: last_applied_version}。
这是 Recovery 的唯一持久化决策依据。
不得引入第二个持久化字段（如 tx_ids、receipt_hash 等）参与 skip 决策。

### 6. tx_id 不得作为持久 Recovery 决策依据

tx_id 是事务标识，不是消费水位。
在"一次 commit 一个 version"的前提下，tx_id 与 version 双射，
持久化 tx_ids 是 Dual Source of Truth——与 version 描述同一命题，
却可独立演化，导致永久漏 replay。

### 7. Process Cache 不得跨重启恢复

进程内可维护"已处理 tx_id/version"的集合用于去重（Process Cache），
但该缓存启动时为空，进程结束即丢弃，严禁从磁盘加载后用于 skip 决策。
跨重启幂等由 version checkpoint 唯一保证。

## 幂等分层

| 层级 | 机制 | 键 | 持久化 |
|------|------|-----|--------|
| Recovery Watermark | version checkpoint | version | 是 |
| Process Cache | 进程内去重集合 | tx_id/version | 否 |
| Projection 副作用幂等 | apply 可重入 | 副作用自身 | 按需 |

### Recovery Watermark

保证：不重复消费已成功投影的 receipt。

### Process Cache

保证：同进程内不重复 apply 同一 receipt。
崩溃后丢失是安全的——Recovery Watermark 仍保证不漏不重。

### Projection 副作用幂等

保证：apply 被调用多次时，外部效果等价于调用一次。
由各 Projection 自行实现（如 FileProjection 的 write 是幂等的）。

## 禁止的做法

1. 禁止持久化 tx_ids 集合并用于 Recovery skip 决策
2. 禁止 apply 失败后递增 version 再重试下一个 receipt
3. 禁止从 checkpoint 恢复 Process Cache 并参与 skip 决策
4. 禁止在 version checkpoint 之外引入第二套消费进度表示

## 与 Veritas 宪法的关系

Forge Recovery Constitution 不独立于 Veritas Constitution。
Recovery Watermark（version）的语义完全由 Veritas 的 commit 序号保证。
若 Veritas 的 version 语义发生变化，本文档必须重新审查。

## 版本

v1.0 — 2026-08-06

基于架构裁决：tx_ids 是 Dual Source of Truth，应删除。
Checkpoint 永远只保存 version。

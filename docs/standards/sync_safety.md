# Forge 同步安全标准

Status: Normative
Scope: World↔workspace 同步安全不变量
Applies to: forge/sync/、forge/projections/file_projection.py、forge/runtime.py 同步路径，以及未来任何同步实现

规范语言：

- 必须 / 严禁 — 强制要求
- 应 / 不应 — 强默认，偏离需说明理由
- 可 — 可选

---

## 1. CONFLICT 下严禁自动推进同步进度标记

当 World↔workspace 同步检测产生 CONFLICT（共同已知状态之后出现未解决分叉）时，系统**严禁**自动推进任何表示「已成功消费或同步该分叉状态」的同步进度标记（checkpoint / watermark）。

### 1.1 符合性要求

- 同步进度标记**必须**只在显式决议之后、真正同步成功后推进。
- CONFLICT **必须**停止同步流程，严禁自动选择任一侧作为权威。
- 分叉路径**严禁**以「跳过但推进进度标记」的方式处理。

### 1.2 可观察失败模式

违反本标准至少产生以下之一：

- 进度标记在 CONFLICT 未解决时仍增大
- 系统声称已同步/已消费，但工作区仍处于分叉状态
- 后续同步工作因虚假水位被跳过

### 1.3 范围限定

本标准只约束**同步进度标记**（当前实现为 SyncState.disk_synced_version）。

**严禁**与 docs/RECOVERY_CONSTITUTION.md 中的 Projection Recovery version checkpoint 混淆——后者追踪恢复用的 receipt 消费进度，不表示磁盘同步成功。

### 1.4 来源

由 docs/WORLD_DISK_SYNC.md §4 经 Normative Promotion Policy v2.1 升格。

---

## 2. 否定测试

以下测试作为本标准的语义否定覆盖：

- tests/test_sync_layer.py::test_conflict_when_both_advance
- tests/test_sync_layer.py::test_external_untracked_file_is_conflict

它们断言：检测到 CONFLICT 后，disk_synced_version 不推进。

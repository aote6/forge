# Forge 在 Veritas 世界中的角色边界

日期：2026-08-17
状态：补充现有文档，不替代 WRI / Recovery Constitution / STATUS

## 现有文档已定义

- STATUS.md：Forge 是 Veritas 上的 Engineering Orchestrator（唯一工程编排入口）
- world_runtime_interface.md：WRI v1.0，Forge 与 Veritas 的接口契约，Forge 是第一个遵循 WRI 的系统软件，不是 WRI 的定义者
- RECOVERY_CONSTITUTION.md：Forge Projection Recovery 的恢复语义，建立在 WRI 保证的 Receipt 契约之上

## 本文档补充什么

现有文档定义了 Forge 是什么、接口长什么样、崩溃怎么恢复。
但没有定义 Forge 在 Veritas 世界中的操作边界：

1. Forge 能否修改 Veritas 内核代码？
2. Forge 能否通过 Veritas 创建新应用？
3. Forge 能否修改 Forge 自身代码？
4. 这三种操作的自举 / 自指问题怎么处理？

## 三种操作模式及边界

### 1. 修改 Veritas 内核代码

现状：Forge 有 Projection 层（RECOVERY_CONSTITUTION 定义），
可以把 Receipt 投影回文件系统。
问题：修改内核后 veritasd 需要重启，Forge 需要处理自举循环。
边界：未定义。当前不启用。

### 2. 通过 Veritas 创建应用

现状：WRI 定义了 Object 创建、State 写入、Link、Capability 等能力。
Forge 理论上可以创建新 Object 作为应用。

状态更新 2026-08-17：
主循环已打通。Intent 执行真正走 WorldSession：
- ExecutionAdapter 到 IntentExecutor 到 WorldSession 到 veritasd 到 Veritas Kernel
- commit 后 Receipt 到 Projection 到文件系统
- Edit Contract 已冻结（P0 Closure）
- CREATE_OBJECT 纯世界对象创建语义已穿透
- capability_grants 跨 veritasd JSON 边界已闭合
- 251 tests passed，含真实 veritasd e2e

### 3. 修改 Forge 自身代码

现状：Forge 代码在文件系统里，不在 Veritas Object 里。
问题：自修改语义未定义——修改何时生效？
边界：未定义。当前不启用。

## 当前优先事项

已完成 2026-08-17：
- Forge 的 Intent 执行走 WorldSession
- commit 后 Receipt 投影回文件系统
- CREATE_OBJECT 语义穿透
- capability_grants 跨边界闭合
- Planner / PlanValidator 支持 create_object operation_type

下一步：
- 处理 test_e2e_veritas_forge 绝对路径历史问题
- 确认 _as_root 与 veritasd SHA-256 state_commitment 输出格式对齐

暂不做：
- 修改 Veritas 内核（自举协议未定义）
- 修改 Forge 自身（自指语义未定义）

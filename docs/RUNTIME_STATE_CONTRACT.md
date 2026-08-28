# Runtime State Contract v1

Type: Contract
Authority: Binding
Status: Active v1
Scope: Forge 主 Runtime 生命周期、同步决策、AgentTask 产生关系


## 0. 目的

Forge 已有多个状态对象，但缺少一份统一的运行时状态模型，导致：

- _pending_action、_subagent_results、SyncState、对话日志各自保存状态片段；
- 没有机器可读的字段回答「现在处于什么阶段、是否在等用户、崩溃后从哪继续」；
- SyncReport（事实）与「用户如何决定同步方向」（决策）没有分离。

本文档只定义三个对象及其关系，不新增业务状态机。


## 1. 三个对象，三类性质

RuntimeState
- 性质: 当前执行生命周期
- 持久化: 是, .forge/runtime_state.json
- 唯一真相: 是

SyncDecision
- 性质: 用户/主 AI 对同步策略点的决议
- 持久化: 是, .forge/sync_decision.json
- 唯一真相: 是

SyncReport
- 性质: 一次同步检测的事实
- 持久化: 否, 可重算
- 唯一真相: 计算真相

SyncState
- 性质: 同步水位事实
- 持久化: 是, .forge/sync_state.json
- 唯一真相: 是

核心原则:

SyncReport 是事实，不是决定。
SyncDecision 是决定，不是水位。
RuntimeState 是执行生命周期，不是 World 状态。
SyncState 是水位事实，只在真正同步成功后推进。


## 2. RuntimeState

### 2.1 最小字段

phase: string
active_subtask_id: string | null
pending: Pending | null

pending 与 recovery 均不定义独立生命周期。
pending 是 RuntimeState 的组成字段。
recovery 是启动时派生的恢复模式，不持久化到 runtime_state.json。

### 2.2 phase 枚举

IDLE
- 无任务在跑，等待用户输入

DISPATCHING
- 主 AI 已决定 spawn，子循环尚未开始

RUNNING_SUBTASK
- 子循环正在执行

PAUSED_SUBTASK
- 子循环 Execution Pause，仅进程内

AWAITING_USER
- 等待用户输入，决策/澄清/确认

COMPLETED
- 本轮任务已结束

BLOCKED
- 机器阻断本轮任务

ABORTED
- 用户或系统中止本轮任务

phase 是运行时唯一权威阶段。任何工具调用、事件发送、持久化行为都不得绕过 phase 自行判断。

phase 不要求固定线性转换。
但任何 phase 变化必须先更新 RuntimeState，再执行具有不可逆副作用的动作。

### 2.3 pending

kind: sync_decision | execution_pause | null
summary: string
payload: dict

execution_pause
- 子循环内确认暂停，不持久化，进程死亡即消失
- 所有方是子循环栈，主 Runtime 不持有
- 不进入 Agent ABI，不进入 AgentResult.status

sync_decision
- 同步策略点等待用户决议，必须持久化
- 所有方是 RuntimeState.pending
- 在 decided 之前，Gate 必须拒绝写类工具与 forge_sync 推进

payload 只存最小续跑所需信息，不存栈对象、不存模型消息。

### 2.4 recovery

mode: none | decision_required | abort
reason: string | null

recovery 不写入 runtime_state.json。
它是启动时读取持久化的 phase 和 pending 后推导出的恢复模式。

恢复规则:

崩溃前 phase 为 IDLE
恢复模式为 none
行为: 正常开始新任务

崩溃前 phase 为 DISPATCHING
恢复模式为 abort
行为: 不续跑，提示重新派发

崩溃前 phase 为 RUNNING_SUBTASK
恢复模式为 abort
行为: 子循环栈不可恢复

崩溃前 phase 为 PAUSED_SUBTASK
恢复模式为 abort
行为: in-process pause 不可恢复

崩溃前 phase 为 AWAITING_USER 且 pending.kind 为 sync_decision
恢复模式为 decision_required
行为: 重新检测 sync，提示用户决策

崩溃前 phase 为 COMPLETED 或 BLOCKED 或 ABORTED
恢复模式为 none
行为: 正常开始新任务

recovery 不创建第二套状态机，不持久化独立文件。


## 3. SyncDecision

### 3.1 定义

SyncDecision 是用户/主 AI 对一次同步策略点的显式决议。

它不是:

- SyncReport，单次检测事实
- SyncState，水位事实
- 对单条 run_command 的 Mutation Confirmation

SyncDecision 只在 SyncLayer.detect() 返回 CONFLICT 或 FAST_FORWARD 时产生。

### 3.2 最小字段

decision_id: string
basis: string
direction: disk_to_world | world_to_disk | abort
status: pending | decided | aborted
created_at: timestamp
decided_at: timestamp | null

basis 与 SyncReport.status 一致，但决议本身独立存储。

### 3.3 与 Mutation Confirmation 的边界

Mutation Confirmation
- 粒度: 单次工具调用
- 触发: 写工具 PAUSE
- 所有方: Execution Pause，子栈
- 持久化: 否

SyncDecision
- 粒度: 同步策略点
- 触发: CONFLICT 或 FAST_FORWARD
- 所有方: RuntimeState.pending
- 持久化: 是

用户确认执行一次 rm，只代表允许这一次 rm。

用户选择 disk_to_world 或 world_to_disk，才代表同步方向决议。

两者不可混同。任何把 Mutation Confirmation 当作 SyncDecision 的路径都视为契约违反。


## 4. 关系图

用户意图
   -> 主 Runtime
   -> RuntimeState.phase = IDLE
   -> 主 AI 判断

主 AI 判断需要子任务时:
   -> 构造 AgentTask
   -> spawn_subagent
   -> RuntimeState.phase = RUNNING_SUBTASK
   -> AgentResult
   -> RuntimeState 更新

主 AI 判断需要同步策略点时:
   -> SyncLayer.detect()
   -> SyncReport，事实
   -> RuntimeState.pending = sync_decision
   -> 用户决定方向
   -> SyncDecision，决定
   -> 需要执行同步时构造 AgentTask
   -> 执行成功后 SyncState 更新

关键约束:

- SyncDecision 在 decided 之前，禁止改变冲突双方的 mutation。
- RuntimeState.pending.kind 为 sync_decision 时，Gate 必须拒绝写类工具与 forge_sync 推进。
- AgentTask 只能由主 AI 在 RuntimeState.phase 允许时构造；子 AI 不产生 AgentTask。
- SyncState 只在真正同步成功后推进水位；SyncDecision 本身不推进水位。
- RuntimeState.phase 的每次转换都必须可持久化；进程死亡后重启能推导恢复模式。


## 5. 不变量

1. RuntimeState 是唯一执行生命周期真相。
2. SyncReport 是事实，SyncDecision 是决定，SyncState 是水位。
3. SyncDecision 与 Mutation Confirmation 永不混同。
4. 崩溃恢复不创建第二套状态机，启动时只推导 RuntimeState.recovery。
5. 子循环栈内状态，包括 Execution Pause，不承诺可恢复。
6. 主 AI 永不直接调用执行工具；执行面只经 spawn_subagent 进入。
7. done_when 的 v1 代理不得冒充目标完成语义。


## 6. 与既有文档的关系

- AGENT_ABI.md 定义 AgentTask 与 AgentResult。
- WORLD_DISK_SYNC.md 定义 SyncReport、SyncState、CONFLICT 行为。
- 本文档定义 RuntimeState、SyncDecision，以及它们与 AgentTask、SyncState 的关系。
- MAIN_AGENT_BEHAVIOR.md 是主 AI 行为契约，在本文档的 lifecycle 边界内执行。

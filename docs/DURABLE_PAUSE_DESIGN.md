# Durable Pause / Sub-Runtime Recovery Design v1

Type: Design
Authority: Binding（约束后续实现）
Status: Draft v1
Scope: 子 AI 执行中断后的最小可恢复断点，及新子 Runtime 的恢复流程

标记约定：
[FACT] = 基于源码/文档确认的事实
[DECISION] = 本设计最终裁定，不留选项
[INFERENCE] = 基于事实的推断

## 0. 命名消歧

代码库已有两个不同含义的 checkpoint：

1. forge/projections/checkpoint.py：Veritas version 级别的 Projection 恢复水位。
2. forge/runtime.py 的 _progress_checkpoint_text / [PROGRESS]：主循环注入模型上下文的瞬时提示文本，不持久化。

本文档定义第三个对象 SubtaskCheckpoint，与以上两者无关：

- 不是 Veritas version 水位。
- 不是注入模型上下文的提示文本。
- 是子任务执行中断后，供新进程判断「能不能恢复、从哪恢复」的持久化指针。

后文一律用全称 SubtaskCheckpoint 或 .forge/subtask_checkpoint.json，不再单独使用 checkpoint 一词。

## 1. 设计目标与非目标

### 1.1 目标

只解决三件事：

1. 子 AI 执行中断后，新进程能否判断「这里曾经有一个跑到一半的子任务」。
2. 如果能，恢复点在哪——哪些工具调用已经是安全的既成事实。
3. 谁决定要不要恢复，新子 Runtime 如何重新开始，而不是机械续接旧上下文。

### 1.2 非目标（硬边界）

- 不序列化子 AI 的 messages 列表。
- 不做子循环 PC 级恢复。
- 不恢复工具执行到一半。
- 优先复用已有 ToolCallRecord JSONL。
- 避免重复副作用，但不保证 exactly-once。
- 设计必须最小。

## 2. 当前事实基础

### 2.1 当前架构真实形态

[FACT] spawn_subagent 是主 Runtime 工具循环里的一次同步函数调用。子循环在同一个 OS 进程、同一条调用栈里跑完，返回 AgentResult。

[FACT] run_subagent 主循环包裹在 try / except Exception 里。正常代码异常会走到 _finalize() 产出 blocked 结果。

[INFERENCE] 真正需要 SubtaskCheckpoint 的是不经过 except Exception 的退出：KeyboardInterrupt、SIGKILL、OOM、宿主机重启等。

### 2.2 R1 的前置缺口

[FACT] RUNTIME_STATE_CONTRACT.md 定义了 phase 含 RUNNING_SUBTASK / PAUSED_SUBTASK，且这两个 phase 崩溃后 recovery.mode 都是 abort。

[FACT] 但 spawn_subagent 闭包从未把 runtime_state.phase 置为 DISPATCHING / RUNNING_SUBTASK，也从未写 active_subtask_id。

[DECISION] 这是必须补的前置缺口。补齐后 RUNNING_SUBTASK 崩溃时 RuntimeState.recovery.mode 依然是 abort（旧栈不可恢复），SubtaskCheckpoint 的可恢复信号是独立于 RuntimeState.recovery 的新信号。

### 2.3 ToolCallRecord 侧的事实缺口

[FACT] 已确认的 API：write_record、get_record、new_tool_call_id、current_timestamp。

[未验证] 是否存在 list_records_for_subtask(project_root, subtask_id)。若不存在，需在最小实现路线补一个只读函数。

## 3. 状态模型

新增独立派生信号 SubtaskRecovery，风格对齐 Recovery：启动时派生，不持久化。

SubtaskRecovery.mode:

NONE
- 没有遗留的 SubtaskCheckpoint

DECISION_REQUIRED
- 发现 SubtaskCheckpoint，且 RuntimeState 交叉校验一致

INCONSISTENT
- 发现 SubtaskCheckpoint，但 RuntimeState 无法确认其生命周期状态

### 3.1 交叉校验规则

无 checkpoint -> NONE

有 checkpoint（subtask_id=C）：

RuntimeState.phase in {DISPATCHING, RUNNING_SUBTASK, PAUSED_SUBTASK}
且 RuntimeState.active_subtask_id == C
-> DECISION_REQUIRED

其他所有情况 -> INCONSISTENT

具体展开：

checkpoint=C, phase=RUNNING_SUBTASK, active=C -> DECISION_REQUIRED
checkpoint=C, phase=IDLE -> INCONSISTENT
checkpoint=C, phase=RUNNING_SUBTASK, active=Y -> INCONSISTENT
checkpoint=C, phase=AWAITING_USER -> INCONSISTENT
checkpoint=C, phase=COMPLETED / BLOCKED / ABORTED -> INCONSISTENT

[DECISION] DISPATCHING 和 PAUSED_SUBTASK 不出现在实际可达判定里。

- DISPATCHING 写入发生在进入 run_subagent 循环之前，而 checkpoint 只可能在
  循环内第一次工具调用完成后才存在。两者在时序上不可能同时为真。
- PAUSED_SUBTASK 从不被持久化写入，磁盘上永远不会读出这个值。

它们保留在 RuntimeState.phase 枚举里，但不参与 SubtaskRecovery 的实际判定。

### 3.2 INCONSISTENT 的语义

INCONSISTENT 不代表 checkpoint 一定是假的，只代表 RuntimeState 不能确认它。

因为 RuntimeState 和 SubtaskCheckpoint 是两个文件，无法原子写入。崩溃可能发生在任意两个文件写入之间，导致合法的 crash window。

INCONSISTENT 是 crash 后可能出现的合法恢复状态，不是 bug。

### 3.3 INCONSISTENT 的生命周期

首次发现 INCONSISTENT：
- 保留 checkpoint
- 不自动恢复
- 不自动删除
- 进入需事实验证的恢复候选

用户仍可显式选择 resume 或 abort。

不做时间策略。不做下次启动自动删除。用户未处理就保留，处理后才终结。

### 3.4 RuntimeState.phase 生命周期

[DECISION] 补齐 phase 驱动，不改变 phase 枚举本身：

IDLE
-> 主 AI 决定 spawn_subagent 前: phase = DISPATCHING, active_subtask_id = 新 subtask_id
-> 进入 run_subagent 循环前: phase = RUNNING_SUBTASK
-> run_subagent 正常返回（任何 AgentResult.status）:
     phase = IDLE, active_subtask_id = None

不引入 PAUSED_SUBTASK 的持久化驱动。PAUSED_SUBTASK 契约定义是「子循环内确认暂停，仅进程内」，不应落盘。

## 4. SubtaskCheckpoint 最小结构

落点：.forge/subtask_checkpoint.json，单槽位，同一时刻至多一份。

[DECISION] SubtaskCheckpointStore 必须使用原子写：先写 .tmp 再 replace，
行为对齐 SyncDecisionStore。避免 checkpoint 自身 torn write 被
「损坏即丢弃」规则悄悄吞掉一次本该可恢复的中断。

subtask_id: string
task: dict            # AgentTask.to_dict() 原样落盘
last_tool_call_id: string
attempt_count: int     # 默认 0
updated_at: float      # unix timestamp

字段存在理由：

- subtask_id：恢复决策的身份锚点，交叉校验和事实查询的必需输入。
- task：恢复时需要 goal/done_when/stop_when/constraints/max_steps 才能重新构造 run_subagent 调用。
- last_tool_call_id：恢复边界指针，划定「这条 id 及之前的一切都是安全的既成事实」。
- attempt_count：恢复尝试次数，只做安全阈值，不是恢复状态机。
- updated_at：供用户/主 AI 判断断点新鲜度。

明确不存的字段：

- 不存 messages / 对话历史。
- 不存步数计数器 / 模型轮次编号。
- 不存 Layer B 的 before/after 快照。
- 不存子 AI 自己的 CONCLUSION/EVIDENCE/UNCERTAIN/NEXT 草稿。

## 5. 中断原因分类

run_subagent 现有的 exit_kind 枚举已覆盖正常退出的全部原因：stop_when / no_tools / max_steps / confirmation_unavailable / user_denied_write / unauthorized_world_change / error。

这七种全部会走到 _finalize()，产出一个合法 AgentResult。SubtaskCheckpoint 在这七种退出路径下全部被清除，因为它们不是中断，是正常结束。

真正需要 SubtaskCheckpoint 存活的只有一类：

PROCESS_INTERRUPTED = 任何没有到达 _finalize() 就导致进程/调用栈消失的退出。

包括：KeyboardInterrupt、SIGKILL、OOM、宿主机断电重启、Termux 后台进程回收。

[DECISION] 不增加持久化的中断原因子分类字段。文件本身的存在就是唯一判别信号。

## 6. checkpoint 写入时机与清除时机

### 6.1 写入时机

唯一写入点：

forge/subagent.py::_execute_tool() 成功返回、ToolCallRecord 已 write_record() 落盘、且 Layer B 未判定 unauthorized_world_change 之后。

写入前必须满足：

- 本次工具调用已产生 tool_call_id，即 _execute_tool 真正执行过。
- 不是被 enforce() 拒绝。
- 不是被 sync_decision pending 拒绝。
- 不是被 PAUSE 且未确认拒绝。
- 未被 Layer B 判定为 unauthorized_world_change。

模型输出但没有工具调用时，不形成 checkpoint。

### 6.2 清除时机

[DECISION] SubtaskCheckpoint 是「AgentResult 成功持久化之前的保险」。

它不能在 _finalize() 内部清除。

因为 _finalize() 只构造 AgentResult 对象，并不负责把它落盘。真正落盘发生在
spawn_subagent 闭包更外层的 append_subagent_result() 调用。

如果 checkpoint 在 _finalize() 里先被清除，而进程在 append_subagent_result()
落盘前崩溃，就会出现：子任务实际已正常完成，但 checkpoint 没了，终态
AgentResult 也没持久化，重启后无任何痕迹证明它发生过。

这是最严重的丢失窗口。

正确清除点必须满足：AgentResult 已成功写入 subagent_results.jsonl 之后。

[DECISION] 唯一清除时机：

spawn_subagent 闭包内，append_subagent_result() 成功返回之后，
再调用 SubtaskCheckpointStore.clear()。

顺序固定为：

1. run_subagent 返回 AgentResult
2. precheck_agent_result 完成
3. append_subagent_result 落盘成功
4. SubtaskCheckpointStore.clear()
5. RuntimeState 复位 IDLE + active_subtask_id = None

如果 append_subagent_result 落盘失败，checkpoint 保留，不进入清除流程。

PROCESS_INTERRUPTED 下 checkpoint 不会被清除，这与既有语义一致。

## 7. restart 恢复流程

新进程启动（Runtime.__init__）:
  1. 按既有顺序加载 RuntimeState
  2. 加载 SubtaskCheckpoint（文件不存在 -> None；空/损坏 -> 安全丢弃不 raise）
  3. 按 §3 规则交叉校验，得到 SubtaskRecovery.mode

NONE -> 正常启动，无额外提示

DECISION_REQUIRED:
  - 向用户/主 AI 呈现中断子任务（subtask_id / task.goal / last_tool_call_id / updated_at / attempt_count）
  - 不自动恢复，等待显式指令

INCONSISTENT:
  - 先加载 ToolCallRecord 做事实校验
  - checkpoint.last_tool_call_id 是否存在？
  - 是否属于同一个 subtask_id？
  - 是否是该 subtask 已完成记录中的合法恢复边界？
    [DECISION] 合法恢复边界 := get_record(last_tool_call_id) 存在
    且 record.subtask_id == checkpoint.subtask_id。
    不要求它是该 subtask 最后一条记录，因为 checkpoint 指针可能落后于
    最后一次成功 write_record。
  - 校验通过 -> 作为可恢复候选，呈现给用户
  - 校验不通过 -> 只能 abort

### 7.1 谁决定恢复

用户，通过主 AI。主 AI 不能替用户决定是否继续一个中断任务。

### 7.2 控制面工具

resume_subtask(subtask_id):

前置检查（DECISION_REQUIRED 和 INCONSISTENT 都必须先执行）：

  0. 查询 subagent_results.jsonl 是否已有该 subtask_id 的终态 AgentResult
     已有终态 -> 拒绝 resume，直接走清理路径（clear checkpoint + phase 复位 IDLE）
     无终态 -> 继续

DECISION_REQUIRED 时：
  1. 校验 subtask_id 与 checkpoint 一致，attempt_count < 3
  2. 从 checkpoint.task 重建 AgentTask
  3. 构造已完成事实摘要
  4. attempt_count += 1 并落盘
  5. phase = RUNNING_SUBTASK
  6. 调用全新 run_subagent，不续旧上下文
  7. 收尾与普通 spawn 相同

INCONSISTENT 时：
  - 先执行 §7 的事实校验
  - 校验通过才允许 resume
  - 校验失败只允许 abort

[DECISION] resume 和 abort 必须有对称的终态检查。任何一条路径都不能在
已有终态 AgentResult 的情况下产生第二个 AgentResult。

abort_subtask(subtask_id):

  1. 查询 subagent_results.jsonl 是否已有该 subtask_id 的终态 AgentResult
  2. 已有终态 -> 不合成新结果，只清理 checkpoint + phase 复位 IDLE
  3. 无终态 -> 合成 status=blocked, status_reason=abandoned_after_process_interrupt 的 AgentResult，写入结果，清理 checkpoint，phase 复位 IDLE

[DECISION] Checkpoint 永远不能覆盖已经存在的终态 AgentResult。

### 7.3 恢复后从哪继续

从 last_tool_call_id 指向的事实之后。不做步数续接。新子 Runtime 从零步开始，max_steps 用 task.max_steps 原值。

### 7.4 如何避免把旧推理当事实

事实摘要只允许来自 ToolCallRecord（tool_call_id / tool_name / status），禁止包含旧子 AI 自己写的 CONCLUSION/EVIDENCE/UNCERTAIN/NEXT。

[DECISION] 事实摘要的读取范围：该 subtask_id 的全部 ToolCallRecord，
不是只读到 checkpoint.last_tool_call_id 为止。

理由：ToolCallRecord 落盘和 checkpoint.last_tool_call_id 推进是两条相邻但
独立的语句。崩溃可能发生在 ToolCallRecord 已落盘、checkpoint 指针还未推进
之间。如果摘要只读到指针，这条已真实发生且可能有副作用的调用会从摘要里
消失，新子 AI 可能重复执行它。

读取全部记录，确保任何已发生的工具调用都不会从恢复上下文中漏掉。

## 8. 副作用重复的缓解边界

三层，层层递减：

### 8.1 机器保证

不重放旧 ToolCallRecord 对应的旧工具调用。恢复不会自动执行任何历史工具。

### 8.2 恢复缓解

将已完成事实以只读摘要提供给新子 AI。新子 AI 根据当前磁盘/World 状态重新判断下一步。

### 8.3 明确不保证

- 不保证新子 AI 不会主动再次产生语义相似的副作用。
- 不做 exactly-once。
- 不做工具语义等价判断。
- 不做跨工具调用的强制去重。

准确表述：不重放历史调用，但不保证新的 AI 决策不会产生新的重复副作用。

## 9. 主 Runtime / 子 Runtime 职责边界

主 Runtime:
- 持有 RuntimeState、SubtaskCheckpoint 的加载与写入触发点
- 持有 resume_subtask / abort_subtask 两个控制面工具
- 不自行判断是否恢复

子 Runtime（新的 run_subagent 调用）:
- 持有本次调用自己的 messages
- 产生新的 ToolCallRecord
- 不知道自己是否是「恢复」出来的

用户:
- 恢复/放弃的最终决策权

主 AI:
- 用户意图到 resume/abort 工具调用的翻译层
- 不能在没有用户明确指示时调用这两个工具

## 10. 与 ToolCallRecord / AgentResult / RuntimeState 的关系

ToolCallRecord:
- 证明具体工具调用已经发生
- SubtaskCheckpoint 只存指针，不复制内容

AgentResult:
- 证明子任务是否已经形成最终结果
- Checkpoint 永远不能覆盖已有终态 AgentResult

SubtaskCheckpoint:
- 只负责告诉系统「这里可能有一个尚未处理的恢复断点」

RuntimeState:
- 告诉系统 Runtime 当前生命周期状态
- 不一致时不能确认 checkpoint，但不代表 checkpoint 是假的

事实优先级：

ToolCallRecord = 已发生事实
AgentResult = 子任务最终结果
SubtaskCheckpoint = 恢复指针
RuntimeState = 当前生命周期状态

三者不要求 crash 后永远一致。RuntimeState 一致时恢复更可信；不一致时退回 ToolCallRecord 做事实校验。

## 11. 明确的不做什么

- 不序列化子 AI 的 messages 列表。
- 不做子循环步数/模型轮次恢复。
- 不恢复工具执行到一半的状态。
- 不支持多子任务并发 checkpoint。
- 不自动恢复。
- 不把旧子 AI 的 CONCLUSION/EVIDENCE 当恢复上下文。
- 不修改 RuntimeState.pending.kind 枚举。
- 不修改 derive_recovery 对 RUNNING_SUBTASK / PAUSED_SUBTASK 的 abort 语义。
- 不做语义级「是否已产生等效副作用」比对。
- 不做跨机器 / 跨 project_root 恢复。
- 不为 checkpoint 增加中断原因子分类字段。
- 不新增独立恢复状态机。
- 不为 INCONSISTENT 新增第三个控制面工具。
- 不做 INCONSISTENT 的自动时间清理。

## 12. 最小实现路线

按依赖顺序：

1. 补齐 RuntimeState phase 生命周期驱动（spawn_subagent 闭包）。
2. AgentTask 补 from_dict（与 to_dict 对称）。
3. 新增 forge/subtask_checkpoint.py（SubtaskCheckpoint + Store）。
4. 如不存在，在 forge/tool_call_record.py 补 list_records_for_subtask。
5. run_subagent 接入 checkpoint 写入/清除。
6. Runtime.__init__ 接入 checkpoint 加载与 SubtaskRecovery 派生。
7. 新增控制面工具 resume_subtask / abort_subtask。
8. system_prompt 行为规则补充。

不在最小实现路线内：

- get_runtime_state() / list_recent_subtasks()
- 全局 STOP/PAUSE/ABORT 信号

## 13. 测试计划

新增 tests/test_durable_pause.py：

1. SubtaskCheckpoint 序列化往返。
2. 写入时机：工具成功且通过 Layer B -> checkpoint 存在；PAUSE 未确认 -> 不更新；Layer B unauthorized -> 不作为恢复边界。
3. 清除覆盖七种 exit_kind。
4. PROCESS_INTERRUPTED 模拟：手工写 checkpoint 文件，新建 Runtime，断言三种 SubtaskRecovery.mode。
5. INCONSISTENT 事实校验：last_tool_call_id 存在且匹配 -> 可恢复候选；不存在 -> 只能 abort。
6. resume_subtask 集成：新子 Runtime 收到事实摘要，不含旧 CONCLUSION。
7. abort_subtask 查证：已有终态 AgentResult -> 不合成新结果；无终态 -> 合成 blocked。
8. RuntimeState phase 生命周期回归。
9. 交叉一致性边界：checkpoint 与 RuntimeState 不一致时正确识别为 INCONSISTENT。

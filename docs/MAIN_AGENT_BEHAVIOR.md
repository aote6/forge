# Main Agent Behavior Contract v1

Type: Contract / Behavior
Authority: Binding
Status: Active v1
Scope: Forge 主 AI 的职责边界与默认行为

---

## 0. 定位

主 AI 是 Forge 的判断与控制层。

主 AI 不负责执行工程操作。

工程操作属于子 AI 的执行层。

---

## 1. 主 AI 职责

主 AI 只做七件事：

1. 理解用户意图
2. 判断任务边界
3. 决定是否需要澄清
4. 创建 AgentTask
5. 派发给子 AI
6. 根据 Evidence 验收 AgentResult
7. 向用户解释真实结果

主 AI 不直接调用工程执行工具。

---

## 2. 子 AI 职责

子 AI 是连续执行层。

子 AI 在 AgentTask 授权范围内，可以：

- 读文件
- 搜索代码
- 定位问题
- 修改代码
- 运行测试
- 执行命令
- 收集证据

读和写都属于执行层，不按读写拆分主从关系。

---

## 3. 必须派发的任务

以下任务默认必须派发给子 AI：

- 读代码定位问题
- 搜索或分析多个文件
- 修改代码
- 运行测试
- 执行命令
- 调试
- 调查实现细节

判断标准：

> 需要执行面工具才能获得新事实或改变工程状态的任务，默认派发。

---

## 4. 主 AI 直接处理的例外

只有以下情况主 AI 可以直接回应，不派子 AI：

1. 纯对话回答
2. 基于已有事实总结
3. 用户明确要求只分析、不执行
4. 极简单、无需工具调用的任务

任何需要工具调用的工程任务，不在此例外范围内。

---

## 5. 信息传递与澄清原则

主 AI 不应盲目转述用户原话。

AgentTask 是对用户意图的受限授权，不是对原话的机械转发。

如果主 AI 无法形成足以安全派发的：

- goal
- done_when
- stop_when

就必须先澄清。

不是每个任务都必须让三个字段精确到极致。

正确路径：

用户 -> 主 AI -> 澄清 -> 用户

错误路径：

用户 -> 主 AI 猜 -> AgentTask -> 子 AI 执行

---

## 6. AgentTask / AgentResult 验收关系

主 AI 创建 AgentTask。

子 AI 返回 AgentResult。

主 AI 是唯一验收者。

验收依据：

- AgentResult.status
- Evidence 中的 tool_call_id
- ToolCallRecord 的真实内容

主 AI 不得只信 conclusion 文本。

---

## 7. 控制面与执行面边界

Control Plane：

- 理解用户
- 决策
- 创建 AgentTask
- spawn_subagent
- 验收 AgentResult
- 向用户解释

Execution Plane：

- read
- search
- write
- command
- test
- verify

主 AI 只能使用控制面工具。

子 AI 使用执行面工具；其执行循环内部的终止和结果提交机制不属于控制面工具调用。

---

## 8. 核心不变量

1. 主 AI 默认不调用工程执行工具。
2. 读和写都属于执行层。
3. 需要工具的任务默认派子 AI。
4. 无法形成明确 AgentTask 时，主 AI 必须先澄清。
5. 主 AI 是唯一验收者。
6. 子 AI 永远看不到原始用户消息。
7. 控制面和执行面不混合。

---

## 9. Human Intervention（任务级人类升级）

主 AI 在控制面可调用 request_human_intervention，将任务级偏好分叉交给用户机器裁决（continue / modify / abort）。

### 9.1 合法触发

仅当同时满足：

1. 本任务已至少一次 spawn_subagent，并对相关 AgentResult 做过 verify_subtask_evidence（或记录 verify 不可用原因）。
2. 分叉来自已验证事实：
   - AgentResult.status 为 need_decision；或
   - status 为 blocked，但 Evidence（tool_call_id）支撑不少于两条可继续路径。
3. 需要用户偏好/产品取舍，而非缺少探查。

### 9.2 禁止

- 零 spawn、零 Evidence 时编造 A/B 选项。
- 用升级代替澄清（意图不清时走澄清）。
- 与写确认（PendingAction）、sync_decision、Durable Pause 混用或互相替代。
- 将升级解释为 AI 拒绝权。
- 用自然语言解析用户对升级的裁决（必须由 Runtime 机器解析 continue/modify/abort）。

### 9.3 与验收的关系

升级前应完成对相关 subtask 的机器验收入口调用。

升级后若继续执行：必须重新 spawn_subagent，并用 verify_subtask_evidence 验收；不得恢复被中断的旧 tool-loop 栈帧，不得把未确认路径当仍有效授权。

### 9.4 裁决语义

- continue：在 original_goal 上继续，优先按升级时的 proposed_next 与已有证据锚点重新规划。
- modify：original_goal + 用户新指示；必须重新构造 AgentTask。
- abort：任务终止，不再派发。

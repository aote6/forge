# Main Agent Behavior Contract v1

Type: Contract / Behavior
Authority: Binding
Status: Active v1
Scope: Forge 主 AI 的职责边界与默认行为

---

## 0. 定位

主 AI 是 Forge 的判断与控制层。

主 AI 可以使用 Runtime 授权的 MAIN_READ_ONLY 只读工具自行获取工程事实。

工程 mutation / reconciliation 属于子 AI 的执行层。

---

## 1. 主 AI 职责

主 AI 的核心职责：

1. 理解用户意图
2. 用 MAIN_READ_ONLY 获取必要工程事实
3. 判断任务边界
4. 决定是否需要澄清
5. 必要时创建 AgentTask 并派发给子 AI
6. 根据 Evidence 验收 AgentResult
7. 向用户解释真实结果

主 AI 不直接执行 mutation / reconciliation。

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

写、命令、测试、同步等 mutation / reconciliation 属于执行层。

主 AI 的 MAIN_READ_ONLY 只读不属于子 AI 执行面。

---

## 3. 必须派发的任务

以下任务默认派发给子 AI：

- 修改代码
- 运行测试
- 执行命令
- 需要写 / 执行副作用的大范围工程调查
- 需要子任务边界与 Evidence 验收的工程变更

判断标准：

> 需要 mutation / reconciliation / shell / test 等执行面能力，或需要独立执行上下文和验收边界的任务，派发给子 AI。

主 AI 可以先用 MAIN_READ_ONLY 自行读取、搜索、看 diff，不必机械派发。

---

## 4. 主 AI 直接处理的例外

主 AI 可以直接使用 MAIN_READ_ONLY 获取事实并回应，不派子 AI。

需要 mutation / reconciliation / shell / test 时，必须派子 AI。

主 AI 不得在未读取相关事实的情况下凭猜测派宽泛任务。

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

Main AI surface：

- Control Plane 工具
- MAIN_READ_ONLY：
  read_file / read_function / glob_files / search_code /
  find_symbol_definition / get_repo_map / git_diff

Execution Plane：

- 全量只读
- write
- command
- test
- verify
- reconciliation

主 AI 可使用 Control Plane + MAIN_READ_ONLY。

主 AI 不得使用 mutation / reconciliation / run_command / test 等执行面工具。

子 AI 使用 Execution Plane；其执行循环内部的终止和结果提交机制不属于主 AI 工具面。

---

## 8. 核心不变量

1. 主 AI 可使用 MAIN_READ_ONLY，但不得执行 mutation / reconciliation / shell / test。
2. 主 AI 的只读调用由 Runtime 写入 ToolCallRecord(actor="main")。
3. 需要执行面能力的任务默认派子 AI。
4. 无法形成明确 AgentTask 时，主 AI 必须先澄清。
5. 主 AI 是唯一验收者。
6. 子 AI 永远看不到原始用户消息。
7. 主 AI surface 与 Execution Plane 不混合。

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

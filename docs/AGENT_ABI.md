# Agent ABI v1.4 - Forge 主 AI 与子 AI 契约

Type: Contract / Interface
Authority: Binding
Status: Active v1.4
Scope: Forge main agent 与 subagent 之间的任务边界

版本历史：
  v1.0 (2026-08-23)  初版
  v1.2 (2026-08-25)  Draft：AgentTask / AgentResult / Evidence 三结构
  v1.3 (2026-08-26)  冻结增补：enforcement 不由主 AI 声明；scope/not_allowed 优先级；stop_when 硬终止；工具映射确定性；主 AI 验收不读 detail
  v1.4 (2026-09-06)  Evidence Provenance 裁定：AgentResult.evidence 权威来源改为 ToolCallRecord 机器投影；模型 EVIDENCE 降级为 model_reported_evidence（advisory）；字段同步到实现 ABI

---

## 0. 定位

Agent ABI 是主 AI 与子 AI 之间的唯一任务契约。

它只回答两个问题：

1. 主 AI 能授权什么
2. 子 AI 必须用什么证据交回结果

它不负责理解用户意图，也不负责执行世界操作。

---

## 1. 硬边界原则

Agent ABI 是权限边界，不是提示词。

- not_allowed 是执行禁区，不是语气建议。
- evidence 是 Runtime 机器投影的真实工具调用引用，不是子 AI 的总结或模型报告。
- stop_when 是硬停止点，不是差不多就停。

这三条如果只存在于 prompt 里，Agent ABI 就不成立。

---

## 1.1 可执行性

Agent ABI 中的约束分为两类：

1. Machine-enforced
   可由执行层直接判定并拒绝。
   包括工具权限、路径范围、命令类别等。

2. Model-evaluated
   需要模型根据真实工具结果判断。
   包括 goal、done_when、stop_when。

只有 Machine-enforced 约束才能作为执行层硬拒绝条件。
Model-evaluated 条件不得被描述为机器可验证事实。

---

## 2. 层次关系

用户意图
  |
  v
主 AI 解释
  |
  v
AgentTask          <- Agent ABI 上界
  |
  v
子 AI 执行
  |
  v
AgentResult        <- Agent ABI 下界
  |
  v
主 AI 验收
  |
  v
Intent / WRI / Veritas

规则：

- 子 AI 永远看不到原始用户消息。
- 子 AI 只认识 AgentTask。
- 子 AI 不负责判断用户意图。
- 主 AI 是唯一验收者。

---

## 3. AgentTask

### 3.1 字段

字段         类型          必须  含义
goal         string        是    单一子任务目标
done_when    string        是    什么事实成立才算完成（v1 不语义求值，见 3.4）
stop_when    string        是    硬停止条件
constraints  dict          是    结构化约束容器（not_allowed / scope / command_class）
subtask_id   string        否    Runtime 生成的子任务 ID
max_steps    int           否    最大循环轮数，默认 15

### 3.2 constraints 结构

constraints 是 AgentTask 的约束容器。ABI-recognized keys：

  not_allowed: 执行禁区黑名单
  scope:       允许探索或修改范围白名单
  command_class: 命令类别约束（可执行文件名或子命令前缀）

主 AI 只提交约束内容，不声明 enforcement level。
执行层根据自身能力决定每条约束能否硬执行（machine）或降级（advisory）。

### 3.3 not_allowed 的约束表示

not_allowed 使用结构化约束，不使用自由文本。

例子：

[
  {"action": "write", "path": "forge_rain.py"},
  {"action": "execute", "command_class": "test"}
]

规则：

- action 表示被禁止的动作类别。
- path 表示路径边界。
- command_class 表示命令类别。
- 无法机器判定的约束只能作为模型指导，不得声明为硬约束。

### 3.4 停止与完成的关系

stop_when 控制什么候停止执行。
done_when 描述什么情况下任务可以视为完成。

v1 中 done_when 不做自然语言语义求值。
实际使用的 completion proxy 见 11.5。

执行结束时：

条件                                      返回
stop_when 满足 且 completion proxy 满足   done
stop_when 满足 但 completion proxy 未满足 blocked
无法继续且未完成                          blocked
存在路径选择需要主 AI 决定                need_decision

禁止把后三种返回成 done。

---

## 4. AgentResult

### 4.1 字段

字段                        类型          必须  含义
status                      enum          是    done / blocked / need_decision
conclusion                  string        是    一句话结论（实现字段；旧文档称 summary，已废弃）
evidence                    Evidence[]    是    机器投影的权威证据（见 5 节）
uncertain                   string        否    不确定、未验证部分
next                        string        否    建议，仅参考
stop_when_met               bool          是    执行循环是否因 stop_when 终止
status_reason               string        是    机器生成的状态理由
raw_conclusion              string        否    子 AI 原始结构化输出（完整文本）
model_reported_evidence     tuple         否    模型报告的 EVIDENCE 引用（advisory，见 5.4）

### 4.2 AgentResult.status 与主 AI 验收的边界

AgentResult.status 是子任务执行/证据契约状态，不是主 AI 的最终验收决定。

子任务执行 → AgentResult.status → precheck/persistence integrity → 主 AI 独立验收

Evidence failure 不等于 engineering task failure。
AgentResult.status = blocked 可能仅表示 completion/evidence contract 未满足，不代表工程执行失败。

### 4.3 next 的定位

next 是 advisory only。

- 不构成控制权。
- 主 AI 可以忽略。
- 子 AI 不能通过 next 反向驱动主 AI。

---

## 5. Evidence

### 5.1 定义

Evidence 是 Runtime 机器投影的权威执行事实引用。

Evidence 不是子 AI 的发现。
Evidence 不是模型报告的 EVIDENCE 文本。
Evidence 是对真实 ToolCallRecord 的机器可验证引用。

### 5.2 字段（实现 ABI v1.3+）

字段          类型     必须  含义
tool_call_id  string   是    真实工具调用 ID
claim         string   是    机器生成的事实标签（仅描述执行事实）
path          string   否    工具输入中的显式 path 字段（原样引用）
quote         string   否    stdout/stderr 提取（MDE v1 恒为 None）

旧文档 source / target / detail 字段已废弃。

### 5.3 Evidence Provenance（v1.4 权威来源）

AgentResult.evidence 的唯一权威来源是 Runtime ToolCallRecord。

机器投影条件（全部必须满足）：
  - actor == subagent
  - subtask_id == 当前 subtask
  - status == success

投影规则：
  - 一个成功 ToolCallRecord 对应一个 Evidence
  - Evidence 顺序继承 ToolCallRecord 顺序
  - tool_call_id 是唯一身份锚点，同一工具可多次调用
  - claim 只能描述工具执行事实（如 forge_sync 执行成功）
  - 禁止 machine claim 生成任务完成或业务结果语义
  - path 只取显式 input path，不做路径推导
  - quote 在 MDE v1 恒为 None

错误调用不投影为 Evidence，但事实仍保留在 ToolCallRecord，可通过 verify_tool_call 查询。

### 5.4 Model-Reported Evidence（advisory）

子 AI 输出中的 EVIDENCE 段是 model_reported_evidence：

  - 用于审计、调试、观察模型的证据选择
  - 不参与 AgentResult.status 判定
  - 不参与 done 判定
  - 不是 verify_subtask_evidence 的事实来源
  - 不是 verify_tool_call 的事实来源
  - 不得覆盖 machine-derived evidence
  - 禁止反向升级为 authoritative Evidence

单向边界：

  ToolCallRecord 直接投影为 AgentResult.evidence (authoritative)
  模型 EVIDENCE 只进入 model_reported_evidence (audit only)
  模型 EVIDENCE 永远不能变成 AgentResult.evidence

---

## 6. 核心不变量

1. 子 AI 不决定任务目标。
2. done 必须具有至少一个可追溯的 machine-derived Evidence。
3. Evidence 必须由 ToolCallRecord 机器生成，模型不能生产事实证据。
4. not_allowed 由执行层强制，违反即任务失败或越界。
5. stop_when 是硬停止点。
6. stop_when 满足但 completion proxy 未满足时只能返回 blocked。
7. 子 AI 不能宣布整个用户任务完成。
8. next 无控制权。
9. Agent ABI 不暴露 Veritas / Intent / Projection。
10. Machine Evidence 不等于 Task Completion。ToolCallRecord.success 只证明工具执行成功，不证明任务完成。

---

## 7. 与 WRI 的对应关系

WRI                         Agent ABI
Capability 限制世界操作      not_allowed 限制子任务行为
Transaction Receipt 证明变化 Evidence 证明子任务观察
软件不能伪造 Receipt         模型不能生产事实证据
Commit 是硬边界              done 是硬验收点
世界不解释软件策略            Agent ABI 不解释用户意图

---

## 8. v1 明确不做

- 多轮主从协商
- 子 AI 主动中断主 AI
- 跨子任务共享记忆
- 子 AI 请求扩大权限
- AgentTask 模板库
- AgentResult 语义评分
- Evidence 语义真伪判断
- done_when 自然语言语义求值
- stdout/stderr 语义提取生成 quote

v1 只做一件事：

把主 AI 的授权和子 AI 的回报，
变成可追溯、可拒绝、可验收的硬接口。

---

## 9. 冻结增补裁定 v1.3

### 9.1 enforcement 不由主 AI 填写

主 AI 只提交约束内容。
执行层根据自身能力决定每条约束能否硬执行：
- 能硬执行的约束，执行层标记为 machine。
- 不能硬执行的约束，执行层降级为 advisory，并回报主 AI。
- 主 AI 不得自行声明某条约束为 machine。

### 9.2 scope.paths 与 not_allowed 的优先级

scope.paths 是白名单，表示允许探索或修改的范围。
not_allowed 是黑名单，表示绝对不能执行的动作或目标。

两者冲突时，not_allowed 优先。

执行层判定顺序：
1. 先检查 not_allowed。
2. 再检查 scope.paths。
3. 任一机器可判定约束被违反，直接拒绝工具调用。

### 9.3 stop_when 是执行循环内的硬终止点

stop_when 不是 AgentResult 中的事后标记。

子 AI 在某一轮判断 stop_when 满足时：
- runtime 必须立即阻止下一轮工具调用。
- 子 AI 直接进入 AgentResult 产出。
- 不允许再补任何工具调用收尾。

如果 AgentResult 已经生成，才标记 stop_when，视为无效协议使用。

---

## 10. 工具映射与约束判定补充裁定 v1.3

### 10.1 工具映射是约束判定的前提

约束判定层必须使用确定性工具映射表。
工具映射表把 tool_name 翻译成 action / path / command_class。

未登记的工具默认拒绝，不允许默认放行。
工具映射表和 schemas.py 必须同步维护。

### 10.2 command_class 只许静态前缀白名单

command_class 只能通过可执行文件名或子命令前缀的静态白名单推导。
禁止用模型或启发式判断命令语义。

无法推导的命令类别记为 unknown。
存在 command_class 约束时，unknown 一律拒绝。

### 10.3 主 AI 验收不读模型叙述

主 AI 验收时必须通过 tool_call_id 独立反查 ToolCallRecord。
不得读取子 AI 生成的 conclusion 或 model_reported_evidence 作为真实性依据。

验收时只接受 actor 等于 subagent 且 subtask_id 匹配当前 subtask 的记录。
actor 等于 main 的记录不得作为子任务 Evidence。

---

## 11. Evidence Provenance 裁定 v1.4

### 11.1 权威来源

AgentResult.evidence 的权威来源是 Runtime ToolCallRecord。
必须由 Runtime 根据 5.3 条件机器生成。

子 AI 输出中的 EVIDENCE 段不构成 AgentResult.evidence 的权威来源。

### 11.2 Model-Reported Evidence

子 AI 报告的 EVIDENCE 仅作为 model_reported_evidence：
  - 用于审计、调试、观察模型的证据选择
  - 不参与 AgentResult.status 判定
  - 不参与 done 判定
  - 不是 verify_subtask_evidence 的事实来源
  - 不是 verify_tool_call 的事实来源

### 11.3 Machine-Generated Claim 边界

machine-generated claim 只能描述 ToolCallRecord 本身的执行事实：

  允许：forge_sync 执行成功 / read_file 执行成功 / pytest 执行成功

  禁止：代码已经修复 / 冲突已经解决 / 测试全部通过 / 任务已经完成

### 11.4 quote 约束

quote 在 MDE v1 机器投影中恒为 None。

未来若引入专门的 stdout/stderr 提取机制，可升级 MDE 版本，
但不得在 v1 中由模型或机器启发式生成 quote。

### 11.5 Completion Proxy（明确标注）

v1 不使用自然语言语义求值 done_when。

实际使用：

  completion_proxy_satisfied 等于 stop_when_met 并且 machine_evidence 数量大于等于 1

这不是 done_when 的语义执行器。
它只表示：执行循环因 stop_when 终止，且至少存在一个机器可追溯的成功工具调用。

---

## 12. precheck_agent_result 语义

### 12.1 职责

spawn_subagent 返回边界，Runtime 用磁盘 ToolCallRecord 对 AgentResult.evidence 做二次验证。

### 12.2 规则

  - 验证 machine evidence 的 tool_call_id 是否仍存在于磁盘 ToolCallRecord
  - unverifiable 的 machine evidence 剥离
  - status 等于 done 且剥离后无 machine evidence 则 demote 为 blocked
  - 不得从 model_reported_evidence 重建 authoritative evidence
  - 不评估 conclusion 语义

### 12.3 acceptance semantics 边界

precheck 的 demote 只表示 evidence contract 未满足，不代表工程任务失败。
主 AI 的独立验收不受 precheck demote 的自动否定。

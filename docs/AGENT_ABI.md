
# Agent ABI v1 - Forge 主 AI 与子 AI 契约

Type: Contract / Interface
Authority: Binding
Status: Draft v1.2
Scope: Forge main agent 与 subagent 之间的任务边界

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
- evidence 是对真实工具调用的引用，不是子 AI 的总结。
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

Evidence 的真实性由 tool_call_id 锚定真实 ToolCallRecord，
而不是由 detail 或模型声明保证。

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
done_when    string        是    什么事实成立才算完成
not_allowed  constraint[]  是    执行禁区，由执行层强制
stop_when    string        是    硬停止条件
scope        string        否    允许探索或修改范围

### 3.2 语义规则

- goal 只能有一个。
- done_when 必须是可观察事实。
- not_allowed 不是建议，是任务边界。
- stop_when 是停止条件，不是完成条件。
- scope 只写范围，不写方法。

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

执行结束时：

条件                                      返回
stop_when 满足 且 done_when 满足          done
stop_when 满足 但 done_when 未满足        blocked
无法继续且未完成                          blocked
存在路径选择需要主 AI 决定                need_decision

禁止把后三种返回成 done。

---

## 4. AgentResult

### 4.1 字段

字段      类型         必须  含义
status    enum         是    done / blocked / need_decision
summary   string       是    一句话结论
evidence  Evidence[]   是    真实工具调用引用
uncertain string       否    不确定、未验证部分
next      string       否    建议，仅参考

### 4.2 next 的定位

next 是 advisory only。

- 不构成控制权。
- 主 AI 可以忽略。
- 子 AI 不能通过 next 反向驱动主 AI。

---

## 5. Evidence

### 5.1 定义

Evidence 不是子 AI 的发现。

Evidence 是对真实工具调用结果的引用。

### 5.2 字段

字段          类型     必须  含义
source        string   是    工具名
target        string   是    被观察对象
tool_call_id  string   是    真实工具调用 ID
detail        string   否    仅用于展示，不作为真实性依据

### 5.3 规则

- 没有真实工具调用，就没有 Evidence。
- 没有 tool_call_id，就不算 Evidence。
- detail 可以是模型写的展示摘要。
- 主 AI 判断完成时只能依赖 source、target、tool_call_id。
- 子 AI 自己的推理只能进 summary 或 uncertain，不能进 Evidence。

tool_call_id 必须对应执行器保存的不可变 ToolCallRecord。

主 AI 可通过该 ID 获取真实工具输入、输出及执行状态。

---

## 6. 核心不变量

1. 子 AI 不决定任务目标。
2. done 必须有可追溯 Evidence。
3. Evidence 必须绑定真实工具调用。
4. not_allowed 由执行层强制，违反即任务失败或越界。
5. stop_when 是硬停止点。
6. stop_when 满足但 done_when 未满足时只能返回 blocked。
7. 子 AI 不能宣布整个用户任务完成。
8. next 无控制权。
9. Agent ABI 不暴露 Veritas / Intent / Projection。

---

## 7. 与 WRI 的对应关系

WRI                         Agent ABI
Capability 限制世界操作      not_allowed 限制子任务行为
Transaction Receipt 证明变化 Evidence 证明子任务观察
软件不能伪造 Receipt         子 AI 不能伪造 Evidence
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

### 10.3 主 AI 验收不读 detail

主 AI 验收时必须通过 tool_call_id 独立反查 ToolCallRecord。

不得读取子 AI 生成的 summary / detail 作为真实性依据。

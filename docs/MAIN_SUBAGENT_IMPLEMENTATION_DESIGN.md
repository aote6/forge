# Main AI → Subagent Implementation Design v1

Type: Design / Implementation
Authority: Binding
Status: Active v2
Scope: 主 AI 从执行者到派发者的实现设计

---

## 0. 依据

本设计基于两份已冻结契约：

- docs/MAIN_AGENT_BEHAVIOR.md v1
- docs/AGENT_ABI.md v1.3

本文档只描述实现方向，不包含代码。

---

## 1. 工具面静态分离

### 1.1 控制面工具

主 AI 唯一可见：

- spawn_subagent
- inspect_tool_call_record（新增，验收用）
- ask_user_clarify（若已存在则复用）
- respond_to_user / finish

### 1.2 执行面工具

子 AI 唯一可见：

- READ_ONLY_TOOL_DECLARATIONS
- MUTATION_TOOL_DECLARATIONS
- RECONCILIATION_TOOL_DECLARATIONS

### 1.3 核心决定

静态分离，无运行时提升通道。

主 AI 永远不获得执行工具。

不存在“确有必要时临时开放”的机制。

主 AI 若需要工程事实，只有两条路：

1. 验收需求 → 用控制面工具反查 ToolCallRecord
2. 工程操作 → 创建 AgentTask 派子 AI

---

## 2. spawn_subagent 成为默认路径

### 2.1 system_prompt 改动方向

- 主 AI 身份改为判断与控制层，不再是工程师
- 工具缺失是派发信号，不是缺陷
- 禁止模拟执行，禁止凭训练知识给看似具体的答案
- 给出任务清单作为分类器

### 2.2 安全 AgentTask 判定

goal：能落到具体文件或模块或现象

done_when：必须能想象用哪个 Evidence 验证

not_allowed：能结构化就结构化

scope：能圈路径就圈

任一项写不出可验证版本 → 先澄清，不硬凑 spawn

---

## 3. 主 AI 直接处理例外

静态实现，不做动态判断。

四个例外：

- 纯对话回答
- 基于已有事实总结
- 用户明确要求只分析不执行
- 极简单无需工具的任务

这些例外共同点：不需要工具调用。

所以不需要运行时判断器，工具面从头到尾固定。

---

## 4. Pending Action Gate 两层修订

### 4.0 现状问题

Gate 只认工具身份，不认工具行为。

run_command 不在 MUTATION_TOOL_NAMES，因此：

- 不触发 WRITE_CONFIRM
- 不被 external change guard 拦截

### 4.1 Layer A：执行前分类

run_command 的 cmd 参数做静态前缀分类。

分类表与 Agent ABI 约束判定层共用同一张表。

分类结果：

| 类别 | Gate 行为 |
|------|-----------|
| read_only | 放行 |
| vcs_write | WRITE_CONFIRM |
| destructive_write | WRITE_CONFIRM |
| compound | 强制 unknown |
| unknown | WRITE_CONFIRM |

复合命令永不拆解语义，一律 unknown。

### 4.2 Layer B：执行后状态 diff

不依赖 tool_name 名单。

对执行面工具调用前后取状态快照。

调用后有变化但未走 WRITE_CONFIRM → 标记未授权变更。

### 4.3 判定优先级（写死）

执行面写操作按以下顺序判定：

1. not_allowed 命中 → 直接拒绝，不进入 confirmation
2. scope 越界 → 直接拒绝，不进入 confirmation
3. 允许范围内但属于需要确认的写操作 → pause，等用户确认
4. 确认通过 → 执行

not_allowed / scope 是授权边界。

confirmation 是授权边界以内的用户批准机制。

两者不可混为一谈。

## 4.4 Layer B 未授权变化

Layer B 检测到“执行层发生了未被 Gate 授权的世界变化”时：

- 不新增独立状态
- 子 AI 返回 blocked
- 具体原因写入 status_reason

---

## 5. Execution Pause 是执行层暂停态

### 5.1 定位

Execution Pause 不属于 Agent ABI。

它不进入 AgentResult.status。

它不是主从协议状态。

它是 Execution Plane 内部的运行时暂停态。

### 5.2 链路

主 AI
  |
  | AgentTask
  v
子 AI Runtime
  |
  +-- read/search/test → 继续
  |
  +-- mutation/write
        |
        v
     Execution Gate
        |
        +-- not_allowed 命中 → 拒绝
        +-- scope 越界 → 拒绝
        +-- 允许但需确认 → Execution Pause
        +-- 允许且无需确认 → 执行
        |
        v
     用户确认
        |
        v
     原子恢复同一个子 Runtime
        |
        v
     继续 AgentTask
        |
        v
     AgentResult
        |
        v
     主 AI 验收

### 5.3 Ownership

- pending 由子 Runtime / Execution Gate 持有
- 主 AI 不持有 pending
- 主 AI 不转发 pending
- 主 AI 不 resume 子 AI
- 主 AI 最终只看到 AgentResult

### 5.4 用户确认路径

用户
  |
  v
Forge Runtime / Execution Gate
  |
  v
确认
  |
  v
恢复对应子 Runtime

这是运行时控制机制，不是 Agent ABI。

---

## 6. 实现范围

### 6.1 schemas.py

- 新增 CONTROL_PLANE_TOOLS 和 EXECUTION_PLANE_TOOLS 常量
- 工具映射表作为唯一 source of truth
- 未登记工具默认执行面且默认拒绝

### 6.2 runtime.py

- 按角色选择工具 schema
- spawn_subagent 构造独立子 Runtime
- 子 AI 看不到原始用户消息
- stop_when 硬终止检查点
- Gate 挂载点迁移到子 AI 写调用
- Execution Pause 暂停与恢复通道（运行时内部，不进 Agent ABI）

### 6.3 system_prompt.py

- 主 AI 和子 AI 分开
- 主 AI：判断控制层，禁止模拟执行
- 子 AI：只讲 AgentTask 四要素和 Evidence 产出

### 6.4 测试

- 主 AI 工具面不包含任何执行面工具
- 工程任务第一步产出是 spawn_subagent
- 不安全 AgentTask 派发前被拦截
- stop_when 硬终止
- 工具映射表一致性
- run_command rm 命中 WRITE_CONFIRM
- Layer B 捕获未授权变更
- 跨 Runtime 确认流

---

## 7. 审核结论

按三个问题审核通过：

1. 主 AI 没有重新获得执行工具
2. 子 AI 没有被拆回读写两个模块
3. Agent ABI 没有被实现细节污染

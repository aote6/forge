# Forge Longevity Guide

Type: Stance / Identity
Authority: Strong Guidance
Status: Active v1
Scope: Forge 的长期演进方向：哪些会过时、哪些会留存、如何让 Forge 活得尽量久

## 0. 前提：没有软件能永远不过时

Linux 会换代，AI 工具更不可能永远不变。

本文不承诺「Forge 的代码永远运行」，只回答一个问题：

> 当 Forge 的代码被替换、模型被升级、架构被重写时，什么东西应该活下来？

答案不是代码，是契约和位置。

## 1. 会过时的东西（不要舍不得）

- 具体的模型（DeepSeek、GPT、未来的模型）
- 具体的 Python 实现（runtime.py、subagent.py）
- 具体的主从形态（今天的 spawn / AgentResult）
- 具体的工具面（read_file、run_command、glob_files）

这些是「当前技术条件下的最优解」，不是「永久真理」。

它们会在模型换代、语言换代、架构换代时被重写。

## 2. 不会过时的东西（要保住）

### 2.1 契约

- Agent ABI：主从协议、Evidence 必须绑定 ToolCallRecord
- RuntimeState Contract：执行生命周期有唯一真相
- SyncDecision 语义：事实、决定、水位三者分开
- 验收链：verify 不读 conclusion，只读 tool_call_id 反查事实

这些是 Forge 当前验证出的长期设计原则。

代码会重写，原则会留存。就像 TCP/IP：实现换了一代又一代，协议活了四十年。

### 2.2 世界居民的位置

Forge 是运行在 Veritas 世界上的第一个软件。

这个历史位置不因 AI 换代而消失。只要 Veritas 还在，Forge 就是「第一个证明这台世界机器能承载复杂软件」的居民。

## 3. 让 Forge 活得久的四条路

### 路 1：壳可以换，契约留住

Forge 的 Python 代码是壳，契约是骨。

重写 Runtime 可以，推翻 Agent ABI 不行。
换模型可以，取消 Evidence 验收链不行。
改工具面可以，拆掉 SyncDecision 语义不行。

### 路 2：AI 相关和世界相关彻底分层

模型是可插拔的决策脑，不是正确性骨架。

ToolCallRecord、Gate、RuntimeState、SyncDecision——这些和模型无关。
它们的存在理由不是「LLM 需要」，是「可信系统需要」。

所以模型换代只换零件，不推翻骨架。

### 路 3：定位从「AI 工具」升到「世界软件」

Forge 不是 AI 编程助手，是 Veritas 世界上的第一个居民。

AI 编程助手会过时，世界居民不会。
Forge 的寿命不取决于 AI 技术，取决于 Veritas。

### 路 4：自我替换是方向，不是继承条件

如果 Forge 能修改自己的代码、替换自己的模型、升级自己的架构，
它就不再是固定软件，是能演化的软件。

但自我替换不是 Forge 身份连续性的必要条件。

一个没有自我修改能力的继承者，仍然可以是完整 Forge。
自我替换是长期演进方向，不是继承门槛。

## 4. 四层连续性

### 4.1 契约连续性

Agent ABI、RuntimeState、SyncDecision、验收链——这些必须被继承。

### 4.2 世界连续性

Forge 必须原生运行于 Veritas，或提供经过明确声明的
Veritas-compatible world interface，并说明兼容层如何保持 Forge 的世界语义。

### 4.3 证据连续性

Evidence → ToolCallRecord 的可追溯验收链必须保留。

### 4.4 用户权威连续性

用户是最终裁决者。任何自我演化都不能绕过。

## 5. 核心契约的来源

本文件不定义新的执行协议，也不取代 Normative Standards。

Forge 的核心契约以 docs/standards/ 中当前有效的 Normative Standards 为准。

本文件只定义一个更高层的问题：

> 当实现被替换时，哪些契约必须继续存在，才能称为 Forge 的继承者？

因此：

- Standards 定义「契约是什么」
- 本文件定义「哪些契约必须被继承」
- 实现定义「契约如何实现」

三者不得混淆。

### Core Contract Set

当前 Forge 的核心契约集合由以下文档定义：

- docs/AGENT_ABI.md
- docs/RUNTIME_STATE_CONTRACT.md
- docs/HUMAN_INTERVENTION_CONTRACT.md
- docs/world_runtime_interface.md

本集合由 docs/standards/README.md 注册表维护。

本文件不复制这些契约的具体内容。

未来核心契约集合发生变化时，以注册表为准；本文件只要求继承者遵守当时有效的集合。

## 6. 继承判定规则

本规则不约束当前 Forge 的实现。

但任何未来版本、重写版本或替代实现，若声称「Forge」「Forge successor」
或「Forge-compatible」，都应接受本规则的继承性审查。

不满足连续性要求的系统，可以是新的系统，也可以借鉴 Forge，
但不应无条件宣称自己是 Forge 的完整继承者。

### 6.1 连续性要求（必须保留）

1. 遵守 docs/standards/ 中当前有效的 Forge 核心契约
2. 能表达 RuntimeState 的生命周期
3. 保留 Evidence → ToolCallRecord 的可追溯验收链
4. 保留用户最终裁决权
5. 原生运行于 Veritas，或提供经过明确声明的 Veritas-compatible
   world interface，并说明兼容层如何保持 Forge 的世界语义

### 6.2 声明要求（必须说明）

6. 明确声明哪些旧契约被废弃、哪些被继承
7. 提供迁移和兼容说明

## 7. 演进纪律

1. 契约 > 实现。实现可以换，契约不能偷偷改。
2. 正确性骨架 > 模型能力。模型越强，越不能放松 Gate 和 verify。
3. 世界位置 > 工具形态。Veritas 在，Forge 就在。
4. 用户最终裁决 > AI 自主。任何自我演化都不能绕过这个。
5. 文档 > 记忆。人会忘，AI 上下文会丢，文档是唯一长存的事实。

## 8. 一句话

Forge 的代码会过时，但 Forge 证明的事情不会。

让 Forge 不过时的方法，不是让它一直跑，
是让它变成「后来者必须遵守的契约」和「世界机器的第一个居民」。

这样 Forge 就不再依赖某一种实现而存在，
而成为一组可以被继承、验证和重新实现的长期契约。

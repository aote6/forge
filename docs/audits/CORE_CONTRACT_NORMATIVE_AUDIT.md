# Core Contract Normative Audit

Type: Audit / Historical / Informational
Authority: Non-normative（治理记录，不具约束力）
Status: Completed（2026-09-01）
Scope: 五份核心契约的条款级审查：哪些语义具备 Normative Standard 性质，哪些仍属实现形式

## 0. 审计目的

Forge 已建立 Core Contract Set（见 docs/FORGE_LONGEVITY.md 与 docs/standards/README.md）。
本审计回答：这五份契约中，哪些条款已经是「不可破坏的行为约束」，哪些只是「当前实现形式」。

核心原则（本次审计最重要的结论）：

> 升格的是语义，不是实现形式。

## 1. 五份核心契约

- docs/AGENT_ABI.md
- docs/RUNTIME_STATE_CONTRACT.md
- docs/HUMAN_INTERVENTION_CONTRACT.md
- docs/WORLD_DISK_SYNC.md
- docs/world_runtime_interface.md

## 2. 条款级分类

### 具备 Normative 性质的语义（应保护）

AGENT_ABI：
- AgentResult status 三值语义：done / blocked / need_decision，禁止伪装
- Evidence 必须绑定真实 tool_call_id → ToolCallRecord
- 主 AI 验收只信 Record，不读 summary/detail
- stop_when 满足但 done_when 未满足 → 只能 blocked
- 子 AI 不见原始用户消息、不宣布用户任务完成

RUNTIME_STATE：
- SyncReport 是事实，SyncDecision 是决定，SyncState 是水位
- 水位只在真正同步成功后推进
- durable pending 只有 sync_decision | human_intervention
- recovery 从持久化状态派生，不建第二套状态机
- phase 变化先于不可逆副作用

HUMAN_INTERVENTION：
- 用户裁决机器可确认（continue / modify / abort）
- request 是 turn boundary：持久化后立即结束当前 turn
- abort → ABORTED，不自动回 IDLE
- pending 未 resolve 前 Gate 拒绝推进
- 与 sync_decision 互斥，启动不自动 resolve

WORLD_DISK_SYNC：
- CONFLICT MUST STOP，不自动覆盖任一侧
- checkpoint/水位表示「实际完成同步」，不是「看过 receipt」
- 禁止 skip 后假推进
- Disk+Git 是文件内容权威

WRI（窄子集）：
- 世界修改经 Transaction，Commit 原子生效
- Receipt 由世界生成，软件不可伪造
- 软件不依赖 Kernel 私有接口

### 仍属实现形式的（不应锁死）

- 字段名、JSON schema、CLI 字符串
- command_class 前缀策略的具体形态
- phase 枚举命名
- .forge 存储路径
- WRI 能力目录整体

## 3. 文档级结论

| 文档 | 建议 |
|------|------|
| AGENT_ABI | 升格语义，不锁 API |
| RUNTIME_STATE | 升格状态语义，不锁状态机代码 |
| HUMAN_INTERVENTION | 升格权威边界，不锁 UI |
| WORLD_DISK_SYNC | 最值得升格，尤其 CONFLICT MUST STOP |
| WRI | 保持 Contract，仅核心子集未来可抽 |

## 4. Governance Priority（非运行时 P0）

这是文档治理工作，不是安全修复。

P1：定义 Normative Standard 的条款级升格机制
P2：Agent Evidence / Acceptance、Human Authority / Turn Boundary、Sync Safety
P3：Runtime Recovery、WRI 核心子集

## 5. 权威冲突提示

- standards/README 将五份列为 Contract/Interface，并写明「列入本表 ≠ 自动升级为 Normative Standard」
- 文档内「Binding / Frozen / MUST」语气不自动等于 Normative
- WRI 声称依赖 Veritas Constitution，但 Forge 仓库内未见 Constitution 文本
- 当前治理真相以 standards/README 层级为准

## 6. 最终层级

FORGE_LONGEVITY
    ↓ 什么必须活下来
Core Contracts
    ↓ 当前契约是什么
Normative Standards
    ↓ 哪些条款具有最高规范强度（待升格）
Implementation
    ↓ 怎么实现
Tests
    └── 证明实现仍符合契约

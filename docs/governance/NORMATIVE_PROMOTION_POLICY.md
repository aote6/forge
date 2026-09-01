# Normative Standard Promotion Policy

Type: Governance Procedure
Status: Candidate — Governance Procedure — 采纳前非规范性
Authority: 采纳前非规范性
Scope: 已存在于 Contract / Interface 文档中的条款，如何正式升格为 docs/standards/ 下的 Normative Standard。

Candidate — Governance Procedure — 采纳前非规范性

本文档定义升格程序，本身不升格任何 Core Contract 条款。

在通过 docs/standards/README.md 已定义的治理入口正式采纳之前，本文档严禁被视为有约束力，严禁被视为 Normative Standard。

本程序严禁赋予自己 Normative 地位。正式采纳（如发生）必须遵循 docs/standards/README.md（包括显式指定和注册表规则）。本程序严禁引入 README 未承认的文档权威层级。

若本程序的已采纳形式与 README 冲突，以 README 层级和 §4 注册表为准，直到两者按 README 变更规则一并修订。

本程序中的规范语言（仅在本程序自身被正式采纳后适用）：

- 必须 / 严禁 — 有效升格所必需
- 应 / 不应 — 强默认
- 可 — 可选

## 0. 目的与非目标

### 0.1 目的

回答：

> 当一条款已存在于 Contract / Interface 文档中，在什么条件下、通过什么流程，它才能成为 Normative Standard？

### 0.2 非目标

本程序严禁被解读为：

- 任何含 MUST / 必须 的文本自动升格
- 将整个 Contract 文件批量搬进 docs/standards/
- 实现工作或测试工作的替代品
- 长寿随笔（见 docs/FORGE_LONGEVITY.md）
- 升格后不经标准修订就改变产品行为的授权
- 对 Normative Standards 与 Constitution 文档之间未裁定优先级的裁决

### 0.3 条款级原则

升格必须作用于稳定的语义条款（或 §7 范围内的连贯条款包），默认不作用于整份文档。

Contract 文档可以继续容纳混合内容：部分条款 Normative（经引用），其余仍为 Contract-only。

## 1. 定义

| 术语 | 含义 |
|------|------|
| 条款 | 一条离散的行为或语义要求，可以在不引用实现标识符的情况下陈述。 |
| 条款包 | 表达一个不变量及其必要闭环的一组条款（见 §7）。 |
| 源 Contract | 在 docs/standards/README.md §5（或等价注册表）中登记为 Contract / Interface 的文档。 |
| Normative Standard | 满足 README §2 且列入 README §4 的文档。 |
| 升格记录 | 一次升格动作的持久审计与决策产物（见 §5）。 |
| 实现形式 | 语言、类型名、字段名、路径、schema、CLI 字符串、模块布局——任何重构时可替换的东西。 |

当前 Core Contract Set（仅作输入；不被本程序升格）：

- docs/AGENT_ABI.md
- docs/RUNTIME_STATE_CONTRACT.md
- docs/HUMAN_INTERVENTION_CONTRACT.md
- docs/WORLD_DISK_SYNC.md
- docs/world_runtime_interface.md

## 2. 升格门槛

一条款只有在以下所有门槛通过后，才可被提议升格。

### 门槛 A — 行为语义（非实现形式）

条款必须约束可观察的系统行为、稳定协议或长期语义不变量。

严禁主要规定：

- Python / Rust 类名或函数名
- JSON 字段名或文件路径
- 特定 CLI 文案或工具标识符
- 当前 Runtime 模块结构
- 临时产品范围，除非重述为持久不变量

跨结构检查（必需）：

问：如果实现语言、Runtime、状态机、模块布局或内部数据模型被替换，该条款是否仍表达同一外部义务？

- 是 → 条款可能仍是语义性的（继续其他门槛）。
- 否 → 条款依赖当前结构，是实现形式或设计选择，严禁升格。

### 门槛 B — 显式规范力

升格文本必须能用清晰的 必须 / 严禁（或 MUST / MUST NOT）表达，使合格实现者能无需猜测意图即判断符合与违反。

描述性架构（如「系统有 X/Y/Z 层」）严禁通过此门槛，除非重写为行为约束。

### 门槛 C — 可验证性

必须存在至少一种实际检测违反的方式：

- 自动化测试
- 状态检查
- 持久日志 / ToolCallRecord / receipts
- 其他机器可检查产物

仅模型判断（如「目标质量」）严禁被当作机器强制真相来升格。

对同时通过门槛 E 的条款，门槛 C 不因「未来测试计划」而满足。见 §3。

### 门槛 D — 跨实现稳定

条款必须在 Forge 用另一语言重写、Runtime 被替换、模型变更或模块重构后仍有意义。

若条款在标识符改名后失效，即为实现形式（门槛 A 不通过）。

### 门槛 E — 不可破坏核心（可操作化）

违反必须威胁 Forge 正确性、安全边界、恢复、用户权威或世界/证据语义中的核心不变量。

通过门槛 E 需要显式失败模式陈述：提案必须写明条款被违反时发生的具体、可观察、可验证的失败模式。仅抽象标签不足。

单独不足：

- 「影响正确性」
- 「影响安全」
- 「影响用户权威」
- 「影响世界语义」

充分风格（所需陈述类型示例，非升格清单）：

- CONFLICT 未解决且同步未成功时，checkpoint / watermark 推进
- 无真实 ToolCallRecord 的 Evidence 被接受为完成证明
- 用户对持久 pending 决策的裁决未解决时，mutation 成功
- 恢复错误地恢复旧 pending 或继续已显式放弃的任务

否定例（仅凭自身严禁通过门槛 E）：

- 仅 UX 一致性
- 代码整洁或模块整洁
- 当前架构便利性
- 文档的历史重要性或影响力
- 主/子 Agent 协作便利，而无上述具体失败模式

「重要」严禁被视为等价于门槛 E。

### 门槛 F — 显式指定

即使 A–E 通过，条款在以下条件满足前仍不是 Normative：

1. 一份 Normative Standard 文档（或章节）陈述它；且
2. 该文档列入 docs/standards/README.md §4；且
3. 升格记录已存档（见 §5）。

重要：Contract 中出现 MUST 严禁被视为门槛 F 满足。

## 3. 测试覆盖规则

对每一条款（或 §7 下的包），升格记录必须报告覆盖：

| 结论 | 含义 |
|------|------|
| Covered | 语义义务被违反时，现有测试失败 |
| Partial | 部分方面覆盖；列出缺口 |
| Missing | 无语义否定覆盖 |

对声称门槛 E 的任何条款：

- 必须至少有一个否定测试，证明符合实现在该条款被违反时失败（或拒绝）。
- 测试必须针对语义违反，而非仅存在/不存在当前字段名、函数名或模块结构。
- 「以后补测试」严禁被接受为完成门槛 E 条款升格的依据。
- Partial 或 Missing 严禁完成门槛 E 条款的升格。

严禁升格条款同时削弱测试，使不符合行为「通过」。

## 4. 升格严禁锁死的东西

升格严禁将以下内容冻结为 Normative，即使它们出现在源 Contract 中：

| 类别 | 示例（非穷尽） |
|------|---------------|
| 语言 / 类型 | Python 类名、dataclass、枚举成员拼写 |
| 序列化 | JSON 字段名、.forge/*.json 路径 |
| Schemas | 特定约束 JSON Schema 形状 |
| CLI / UX 文案 | 精确提示字符串、选项标签拼写 |
| 工具面 | 当前工具名 |
| Runtime 拓扑 | 模块文件、类边界、单进程假设 |
| 存储布局 | 同步元数据物理存放位置 |
| 模型提供方 | 特定 LLM 厂商或 API |

这些可以作为当前实现的信息性约束保留在 Contract 中。Normative 文本应用实现中立语言陈述语义不变量，并可注明当前名称仅作示例。

## 5. 角色

Forge 可能是单维护者。流程仍必须产生持久记录。

| 角色 | 职责 |
|------|------|
| Proposer | 提交升格提案。 |
| Auditor | 执行条款级门槛审查和测试覆盖审查；若与 Proposer 同一人，须在记录中披露双重角色。 |
| Approver | 维护者权威，将变更采纳进 README §4 和 Normative 文档。在多党治理存在前，Approver 即仓库维护者。 |
| Implementer | 在 §6 要求时负责代码/测试对齐。 |

允许自我批准，但必须在升格记录中显式写明（日期、身份、双重角色披露）。

## 6. 实现符合性（无延期升格）

若候选条款已知在生产实现中不成立，升格严禁完成。

项目必须：

1. 修复实现使行为符合；或
2. 收窄/修订候选条款，使真实所需义务与即将登记的内容一致，且实现符合该措辞。

没有 deferred-conformity、transitional-normative 或「先登记后修代码」的路径可完成 Normative 升格。

已知不符合会阻断门槛 F 完成（登记），无论 A–E 纸面是否通过。

若有意的产品变更与旧 Contract 文本分歧，严禁升格旧文本。先修订 Contract，再考虑修订后义务的升格。

## 7. 条款包边界

默认：一个包 = 一个可以独立违反、独立判断、独立测试的不变量。

一个包只有在多个句子构成同一不变量的必要闭环时，才可包含多个句子。

允许的包形状示例（说明性，非升格）：

- Evidence 必须绑定真实 tool_call_id / ToolCallRecord；且
- 无可追溯 Evidence 时，status 严禁为 done。

严禁将无关不变量绑在一个包里，例如：

- Evidence 验收链 + 同步 CONFLICT 停止/水位安全 + 人机权威 / turn boundary

作为一个升格包。

跨不变量工作必须分开提案，各自有自己的门槛、测试和记录。

## 8. 升格流程

升格必须按以下步骤顺序执行。跳过步骤必须使升格无效。

### Step 1 — 提案

Proposer 创建升格提案，包含源路径、条款定位、实现中立措辞、门槛论证、门槛 E 失败模式、非目标、包边界论证和草拟位置。

### Step 2 — 条款级审计

Auditor 必须对照提案措辞重新检查门槛 A–E，拒绝整文档升格（除非每条保留条款独立通过），执行包边界，并在不静默选择胜者的情况下标记冲突。

### Step 3 — 测试覆盖审查

Auditor 必须应用 §3。没有语义否定测试的门槛 E 条款必须失败。

### Step 4 — 实现符合性检查

符合 → 继续。不符合 → 严禁完成升格。与旧文本有意分歧 → 先修订 Contract。

### Step 5 — 批准

Approver 必须验证：门槛通过、包边界受尊重、测试满足、符合性满足、无未解决冲突、措辞实现中立。

### Step 6 — 撰写 Normative 产物

必须在 docs/standards/ 下新增或更新文档，状态为 Normative，定义范围，只含已升格条款（加最小上下文），使用 必须/严禁 语言。严禁在自己正文中声称高于其他 Standard 或 Constitution 文档。严禁复制整个源 Contract 正文。

### Step 7 — 登记到 docs/standards/README.md

必须加入 §4 Current Standards，附一行角色描述。严禁从 §5 移除源 Contract，除非单独弃用决定。目录成员资格若无 §4 列表，严禁算作升格。

### Step 8 — 源 Contract 标注

应标注源 Contract，说明哪些条款现已 Normative 及位置。严禁静默删除仍指导实现的 Contract 文本。

## 9. 升格后的权威

Normative Standard（§4）在其声明范围内约束行为。Constitution 约束子系统不变量。Contract / Interface 可混合升格引用和非升格规则。Longevity / Stance 是继承叙事，不是 Normative 来源。Architecture / Design 未经修订严禁覆盖 Normative。Implementation 是当前事实。Tests 验证；存在 Standard 时严禁单独定义规范。

若 Normative Standard 与 Constitution 文档存在未解决语义冲突，本程序严禁决定优先级。冲突必须进入治理决议。实现者严禁私下选择一侧。

两个 §4 标准冲突：必须通过修订或显式层级说明解决。严禁临时选择。严禁靠后一个 Standard 正文自封优先级解决。

若代码与 Normative Standard 不一致：修代码或正式修订标准。「当前代码是权威」严禁抹除已发布标准。

Longevity 严禁用作门槛 E 或门槛 F 的唯一证据，严禁施加新的可执行义务。

## 10. 修订、破坏性变更和撤回

编辑性变更：不改变符合性判定的澄清可以不随代码变更落地。

语义变更必须：与代码同一变更集更新 §4 文本；更新/新增测试；记录破坏性变更；获得批准；对新措辞重新检查门槛 A–E。

撤回必须：在 §4 文档中移除或标记废弃该条款；更新 README §4 描述；说明源 Contract 是否仍保留较弱的 Contract 级规则；提供迁移说明。

## 11. 禁止的升格模式

严禁：

1. 仅因 Contract 用了 MUST 就升格
2. 无 §4 列表和门槛就把文件移进 docs/standards/
3. 一步升格整个 Core Contracts 而不做条款审计
4. 锁死实现形式（§4）
5. 把审计或 Longevity 散文当静默 Normative 来源
6. 靠「实现方便的」解决文档冲突
7. 让测试在不修订标准的情况下重新定义已发布标准
8. 在生产行为已知不符合时完成升格（§6）
9. 把无关不变量打包（§7）
10. 让 Standard 正文声称高于其他 Standard 或 Constitution
11. 在正式采纳前把本 Candidate 当有约束力

## 12. 最小升格记录模板

Title、Date、Proposer、Auditor、Approver、双重角色披露、源 Contract、条款定位、拟升格措辞、包边界、门槛 A–E 结果、失败模式、被拒绝的过宽措辞、明确不升格、冲突、测试覆盖、否定测试路径、实现符合性、Normative 目标路径、README §4 条目、源 Contract 标注计划、破坏性变更。

## 13. 本程序的采纳

本文档是 Candidate。正式采纳前，它是非规范性、非约束性的。

采纳本程序严禁升格任何 Core Contract 条款。

采纳本程序严禁仅凭自身创建新的 Normative Standard，除非 README 显式将其列入 §4。

# Veritas World Runtime Interface v1.0

```
Type: Contract / Interface
Authority: Binding
Status: Active
Scope: World Runtime Interface between Forge and Veritas-facing runtime surfaces
```


版本：v1.0

依赖：Veritas Constitution v0.2

---

# 0. 总则

## 0.1 定义

World Runtime Interface（WRI）是 Veritas Machine 提供给世界软件的唯一标准接口。

任何运行于 Veritas 世界中的软件，都必须通过 WRI 使用 Veritas Machine。

WRI 不属于任何单一软件。

Forge 是第一个遵循 WRI 的系统软件，但不是 WRI 的定义者。

---

## 0.2 分层关系

Veritas 的软件体系：

Veritas Constitution | v Veritas Machine | v World Runtime Interface (WRI) | v World Software | +-- Forge +-- TinyOS +-- Debug Monitor +-- Application

关系：

- Constitution 定义机器必须是什么。
- Machine 实现 Constitution。
- WRI 定义软件如何使用 Machine。
- 软件依赖 WRI，不依赖 Kernel 内部实现。

---

## 0.3 WRI 原则

### 抽象原则

WRI 描述世界能力。

WRI 不描述：

- Kernel 内部结构。
- KernelCall。
- RPC。
- 数据格式。
- 编程语言。
- 存储实现。

---

### 稳定原则

WRI 是世界与软件之间的长期契约。

Kernel 可以：

- 重构。
- 替换 Engine。
- 升级 WAL。
- 改变内部数据结构。

只要 Constitution 和 WRI 保持兼容，上层软件无需修改。

---

### 最小原则

WRI 只提供软件运行所必须的世界能力。

Kernel 内部存在的能力，不代表必须进入 WRI。

---

### 组合原则

WRI 提供世界原语。

WRI 不提供软件策略。

例如：

WRI 提供：

- Object。
- Transaction。
- Link。
- State。

但：

- 项目管理。
- 代码组织。
- AI 推理。
- 文件管理。

属于上层软件。

---

# 1. Identity（身份）

## 1.1 定义

Veritas 世界中的软件必须拥有世界身份。

身份由一个 Object 表示。

软件通过身份 Object：

- 参与世界交互。
- 持有 Capability。
- 创建 Transaction。
- 执行世界操作。

---

## 1.2 世界承诺

世界提供：

- 身份建立能力。
- 身份查询能力。
- 当前执行主体识别能力。

身份 Object 与普通 Object 遵循相同生命周期规则。

---

## 1.3 身份规则

- 一个执行主体在同一时刻拥有唯一当前身份。
- 所有 Transaction 都属于某个身份。
- Capability 权限判断以身份作为主体。
- 身份 Object 死亡后，该身份失去世界操作能力。

---

# 2. Transaction（事务）

## 2.1 定义

Transaction 是 Veritas 世界状态变化的基本边界。

所有世界修改必须通过 Transaction 完成。

Transaction 同时表示：

- 状态变化范围。
- 执行上下文。

---

## 2.2 世界承诺

世界提供：

- 创建 Transaction。
- 提交 Transaction。
- 中止 Transaction。

---

## 2.3 Transaction 生命周期

Created | v Active | +------ Commit ------> Applied | +------ Abort -------> Discarded

---

## 2.4 规则

- Transaction 修改不会立即成为世界状态。
- 提交成功后修改原子生效。
- 提交失败时世界保持原状态。
- 冲突检测发生在提交阶段。
- Transaction 不允许嵌套。
- 一个执行主体默认只有一个活跃 Transaction。

---

# 3. Observation（观察）

## 3.1 定义

软件必须能够观察世界状态。

Observation 是只读能力。

---

## 3.2 世界承诺

世界提供：

### 世界摘要

软件可以获得：

- 当前世界版本。
- 当前 root_hash。
- Object 数量。
- 世界状态摘要。

---

### Object 查询

软件可以：

- 查询 Object 列表。
- 查询 Object 状态。
- 查询 Object 类型。
- 查询 Object 生命周期状态。

---

### Link 查询

软件可以：

- 查询 Object 之间的连接关系。
- 获取 Link 类型。

---

### State 读取

软件可以读取：

- 指定 Object 的 State。

读取受 Capability 约束。

---

## 3.3 规则

- Observation 不修改世界。
- Observation 不产生状态变化。
- Observation 可以在 Transaction 内读取一致性视图。
- 软件不能读取没有权限访问的状态。

---

# 4. Mutation（修改）

## 4.1 定义

Mutation 是世界状态变化能力。

所有 Mutation 必须发生于 Transaction 内。

---

## 4.2 Object 操作

世界提供：

### Object 创建

软件可以请求创建新的 Object。

创建成功后：

- 世界分配唯一 ObjectId。
- Object 进入生命周期管理。

---

### Object 销毁

软件可以请求销毁 Object。

规则：

- 不可逆。
- 生命周期规则由 Constitution 定义。
- Link 级联行为由 Constitution 定义。

---

### Object 冻结

软件可以请求冻结 Object。

冻结后：

- Object 状态不可修改。
- 生命周期规则由 Constitution 定义。

---

## 4.3 Link 操作

世界提供：

- 创建 Link。
- 删除 Link。

Link 必须包含 Link 类型。

Link 行为由 Constitution 定义。

---

## 4.4 State 修改

软件可以修改：

- 自身拥有权限的 Object State。

修改：

- 在 Transaction 中暂存。
- Commit 后生效。

---

## 4.5 修改规则

Mutation 必须满足：

- 当前身份拥有必要 Capability。
- Object 状态允许修改。
- Transaction 有效。
- 修改符合 Constitution。

---

# 5. Verification（验证）

## 5.1 定义

软件必须能够确认：

- 操作是否成功。
- 世界是否进入预期状态。

---

## 5.2 世界承诺

世界提供：

### Transaction Receipt

每次成功提交产生 Receipt。

Receipt 用于证明：

- 某次 Transaction 已完成。
- 世界接受该变化。

---

### Root Hash

世界提供确定性状态摘要。

root_hash 表示：

- 当前世界状态承诺。

包含：

- Object。
- State。
- Link。
- Capability。

---

### World Version

世界版本：

- 单调递增。
- 表示世界演化顺序。

---

## 5.3 验证规则

- Receipt 由世界生成。
- 软件不能伪造 Receipt。
- 相同初始状态和相同执行结果应产生相同世界状态。

---

# 6. Compatibility（兼容）

符合 WRI v1 的软件：

可以运行于所有符合 WRI v1 的 Veritas Machine。

软件不得依赖：

- Kernel 私有接口。
- Engine 内部结构。
- WAL 内部格式。
- 存储实现。

---

# Appendix A：WRI 能力分类

| 分类 | 能力 |
|-|-|
| Identity | 获取世界身份 |
| Transaction | 创建事务 |
| Transaction | 提交事务 |
| Transaction | 中止事务 |
| Observation | 查询世界摘要 |
| Observation | 查询 Object |
| Observation | 查询 Link |
| Observation | 读取 State |
| Mutation | 创建 Object |
| Mutation | 销毁 Object |
| Mutation | 冻结 Object |
| Mutation | 创建 Link |
| Mutation | 删除 Link |
| Mutation | 修改 State |
| Verification | 获取 Receipt |
| Verification | 查询 root_hash |
| Verification | 查询 World Version |

---

# Appendix B：与 Constitution 的关系

WRI 能力来源于 Veritas Constitution。

对应关系：

| WRI 能力 | Constitution 基础 |
|-|-|
| Identity | Object |
| Transaction | Transaction |
| Observation | Memory / Object |
| Mutation | Object / Link / Memory |
| Capability | Capability |
| Verification | Deterministic State |

此映射用于说明 WRI 与 Constitution 的关系。

WRI 不依赖 Constitution 的具体实现方式。

只要求：

符合 Constitution 的 Veritas Machine 必须能够提供 WRI 所承诺的世界能力。

---

# Appendix C：版本规则

WRI 使用版本管理。

WRI v1：

定义：

- 身份模型。
- Transaction 模型。
- Observation 模型。
- Mutation 模型。
- Verification 模型。

后续版本必须：

- 保持向后兼容。
- 不破坏已有软件。
- 不改变 Constitution 基础语义。

---

结束。

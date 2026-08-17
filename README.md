# Forge

Transactional Software Engineering Runtime for LLMs.

Forge 是一个带状态管理能力的 AI 编排系统。它让 LLM 的操作先进入可审计、可回滚、可验证的 Veritas World 状态层，通过校验后再决定是否物化到磁盘。

## 核心设计

机器提供事实，LLM 决策，Validator/Constitution 只 ACCEPT/REJECT，绝不 semantic repair。

任务生命周期：

UNDERSTANDING → PLANNING → CHECKING → EXECUTING → VERIFYING → COMPLETED/FAILED

每次操作必须先写 Veritas World 事务，通过检查后才投影到文件系统。

## 目录

adapters/        模型适配器（DeepSeek/Gemini）、ExecutionAdapter、HubClient
context/         RepositoryIndex、快照、impact/obligations 推导
core/            安全路径解析、edit_contract、校验器
intents/         Intent 数据模型与 IntentExecutor
memory/          检查点存储
orchestrator/    EngineeringOrchestrator 六阶段状态机
projections/     文件/Git/索引投影（World → 磁盘）
protocols/       Plan/ChangeProposal/OperationContract 等协议模型
tools/           AI 可调用工具
verification/    验证请求与结果处理
world/           WorldRuntime、WorldSession、Veritas 适配

## 快速开始

export DEEPSEEK_API_KEY="你的key"

cd ~/forge && python3 dp.py

## 测试

python3 -m pytest tests/ -q

当前全量：301 passed, 1 xfailed（xfailed 是反模式护栏，记录禁止 semantic repair）

## 关键契约

CANONICAL_PLAN_OPERATION_TYPES = {modify, create_file, delete_file, create_object}

create_object 是纯运行时操作，不修改源码，不要求 content/target_files/mutation obligations。

LEGACY_PLAN_OPERATION_ALIASES = {create: create_file, delete: delete_file}

## Hub 工具

Forge 通过 HubClient 调用三个外部能力：

zhiwang  仓库快照/文件树
lu       Constitution 内容规则检查
sms      测试/构建验证

Hub 不可用时 fail-closed，不静默降级。

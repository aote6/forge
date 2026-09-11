# Escape Pod：代码考察记录

这份文档记录 Escape Pod 在提出阶段对 Forge 源码进行考察后已经确认的事实。

它不是 Escape Pod 的施工设计，也不是未来实现方案。

它的作用只有一个：避免未来真正需要 Escape Pod 时，再从零开始重新考察 Forge 的执行结构。

## 考察得到的核心结论

Escape Pod 不需要成为第二个 Forge。

Forge 当前已经存在一套独立于 Main AI 治理层的执行基础。

如果未来真实事故证明 Forge 需要一个位于 Forge 外部的执行体，那么首先应该重新确认并复用这套执行基础，而不是复制 Forge 的 Runtime、Main AI 治理、任务生命周期或事务系统。

今天的考察已经确认，Forge 可以大致分成两个不同层次：

- Forge 的治理与控制层
- 实际执行工具和 Veritas 事务基础

Escape Pod 需要脱离的是前者，而不是把后者重新造一遍。

## Runtime 中的 Main AI 边界

源码位置：

- forge/runtime.py

当前 Runtime 的 Main AI 工具循环包含 Forge 自身的身份和治理判断。

其中 _main_tool_policy_denied() 是 Main AI 的硬性工具政策。

它允许控制面工具和 Main AI 明确允许的最小只读工具集合，同时阻止 mutation、reconciliation 等执行工具直接由 Main AI 使用。

因此，这个函数不是一个通用的工具执行器。

它表达的是：

- Main AI 在 Forge 中应该拥有什么执行权。

这一区别非常重要。

未来 Escape Pod 如果位于 Forge 外部，就没有理由简单复制 _main_tool_policy_denied()，因为那相当于把 Forge 的 Main AI 身份政策带到了 Forge 外部。

## Forge 的 Guard 不等于执行基础

当前 Runtime 在真正执行工具之前，还存在多层 Guard。

已经确认的主要 Guard 包括：

- _guard_path_map_degraded()
- _guard_pending_verify()
- _guard_external_change()
- Human Intervention 相关检查
- SyncDecision 相关检查

这些机制不能全部视为同一种东西。

_guard_pending_verify() 明确属于 Forge 当前任务的工程治理流程。它根据 WorkingSet、verify targets 等状态限制后续 mutation。

_guard_external_change() 也明显属于 Forge 的同步和任务生命周期。它处理外部磁盘变化、World 不可用、sync decision 等问题，并要求进入 Forge 的同步流程。

这些机制体现的是：

- Forge 当前如何管理一个正在进行的工程任务。

它们不是 ToolExecutor 或 IntentExecutor 的基本执行能力。

因此，Escape Pod 如果未来需要脱离 Forge 治理，不应该直接复制整套 Guard 链。

但这不代表所有 Guard 都可以被忽略。

_guard_path_map_degraded() 涉及 World 与路径映射之间的可信度。

如果未来 Escape Pod 仍然操作同一个 World 或同一套路径映射，就必须重新确认这种底层状态是否仍然安全。

所以这里必须区分：

- Forge 的任务治理可以脱离。
- World 和 Veritas 的真实状态不能因为脱离 Forge 就消失。

## ToolExecutor 是现有执行基础

源码位置：

- forge/runtime.py

当前 ToolExecutor.execute() 本身并不承担 Main AI 的身份判断。

它做的事情主要是：

- 根据工具名称寻找实际工具函数
- 检查工具是否存在
- 处理调用参数
- 记录调用历史
- 处理连续失败
- 调用实际工具
- 将结果转换成 ToolResult
- 处理执行异常

其中 mutation 工具并不是由 ToolExecutor 自己实现事务。

源码中的明确注释说明：

- mutation 工具内部通过 IntentExecutor + WorldSession 完成 transaction begin、commit 和 abort。

因此 ToolExecutor 更接近通用执行入口，而不是 Forge 的治理层。

## make_tools 已经能够提供完整执行工具

源码位置：

- forge/tools/__init__.py

当前生产 Runtime 使用：

- make_tools(..., allow_mutation=True, ...)

在满足 WorldRuntime 和 ProjectionManager 条件时，make_tools() 会创建 IntentExecutor，并通过 make_intent_tools() 加入 mutation 工具。

这意味着：

- Forge 并不是只有 Main AI 自己能够执行 mutation。
- 实际 mutation 能力已经被封装在工具集合和 IntentExecutor 中。

Subagent 当前使用的 execution-plane tool set，也是从这个已经存在的工具集合中筛选出来的。

因此未来 Escape Pod 没有必要重新实现一套 mutation API。

## IntentExecutor 是最重要的执行底座

源码位置：

- forge/intents/executor.py

IntentExecutor 的职责已经非常明确：

- 它是事务编排器。
- 它负责把语义 Intent 翻译成 Veritas 原语操作。

典型执行过程是：

- 验证 Intent。
- 创建 World session。
- 执行对应的 World 操作。
- 如果执行失败，abort session。
- 如果执行成功，commit session。
- 返回 Receipt 和 TransactionDelta。

源码还明确表明，IntentExecutor 本身不知道文件系统、Git 或 Forge 的同步治理。

这是今天考察中最重要的发现之一。

因为它说明：

- Forge 的语义 mutation 能力与 Forge 的同步治理并不是不可分割的一体。

未来 Escape Pod 如果需要执行真正的 World mutation，可以优先从这一层重新建立调用关系，而不是复制整个 Forge Runtime。

## intent_tools 是语义工具与事务执行之间的连接

源码位置：

- forge/tools/intent_tools.py

这里的语义工具将具体工具调用交给 IntentExecutor。

成功完成 World commit 后，还会处理 ProjectionManager 等相关的路径映射同步。

如果 path map 更新失败，代码会将 World 标记为 degraded，或者记录 degraded component。

因此这里同时连接了两个层次：

- IntentExecutor 负责实际 World transaction。
- Projection 和 path map 属于 Forge/World 外围状态的一部分。

未来 Escape Pod 如果复用这些工具，必须根据事故确认哪些外围 Projection 或 Forge 状态仍然需要，不能因为“复用执行底座”就默认整个 Forge 工具层都可以原样搬走。

## forge_sync 不属于 Escape Pod 的基础执行能力

源码位置：

- forge/tools/__init__.py

当前 make_tools() 中的 forge_sync() 包含大量 Forge 特有的同步治理逻辑。

包括：

- SyncDecision
- pending 状态
- ReconcileAttemptStore
- recovery
- disk_to_world
- world_to_disk
- sync watermark
- human intervention
- supersede
- reconciliation 生命周期

这些东西不是通用 mutation 执行基础。

它们解决的是：

- Forge 如何管理 Disk、World、Git 之间的同步和一致性。

因此未来 Escape Pod 不能因为“需要 mutation”就把 forge_sync() 整套搬过去。

如果某一次真实事故确实涉及 Disk、World 或 Git 恢复，再根据事故单独判断需要哪些同步能力。

## Control Plane 与 Execution Plane 的历史确认

历史提交已经确认 Forge 曾经明确进行过 Control Plane / Execution Plane 分离。

相关历史事实：

- a7819bca
- 该提交冻结 Main Agent Behavior Contract v1，明确 Main AI 属于 judgment/control plane，Subagent 属于 execution plane。

相关提交：

- fc6030db
- 该提交进一步将 control-plane tool schemas 与 execution-plane tool schemas 分离。

其中 Control Plane 包括：

- spawn_subagent
- verify_tool_call
- todo_write
- todo_list
- submit_plan

Execution Plane 则包含实际 read、mutation 和 reconciliation 工具。

这说明当前 Forge 的架构演化并不是偶然把工具藏起来，而是有明确的身份边界：

- Main AI 的职责与实际工程执行能力本来就是两个不同层次。

这也是为什么 Escape Pod 不应该通过恢复 Main AI 的旧工具权限来实现。

## 不能重复犯的两个错误

今天的考察排除了两个看起来简单、实际上方向错误的方案。

第一个错误是复制第二套 Forge。

如果把 Runtime、Main AI、Subagent、ToolExecutor、IntentExecutor、Veritas transaction 等全部重新复制一份，Escape Pod 就会变成第二个 Forge。

这会把一个极少使用的外部救援能力变成一套新的长期系统。

目前没有证据支持这种复杂度。

第二个错误是在 Forge 内增加 emergency mode。

例如在 Runtime 中增加一个 emergency flag，然后允许 Main AI 在该 flag 下绕过原有 Guard。

这并没有真正建立 Forge 外部的执行边界。

它只是让 Forge 在特殊情况下违反自己的正常规则。

如果 Forge 本身已经损坏、不可信或无法运行，这种方案反而无法解决根本问题。

## 今天已经确认的最小架构方向

如果未来真实事故证明 Escape Pod 必须实现，目前最值得保留的方向只有：

- 外部独立执行体。

它拥有自己的最小运行循环。

它根据任务需要获得有限的工具集合。

真正需要修改 World 时，优先复用已经存在的 IntentExecutor、WorldRuntime 和 Veritas transaction 基础。

它不继承 Forge 的 Main AI 治理流程。

它不继承 Forge 的任务同步生命周期。

它不复制 Forge 的事务系统。

它不因为离开 Forge 就绕过 Veritas 对真实 World 状态的约束。

至于具体应该带哪些工具，目前没有理由提前确定。

## 当前没有确定的东西

以下内容今天没有进行实现，也不应该被这份文档伪装成已经设计完成：

- Escape Pod 的启动方式
- 进程边界
- 独立运行时的具体结构
- 工具白名单
- 工具权限模型
- 外部任务协议
- 工单格式
- 返回流程
- Forge 完全失效时的恢复流程
- Forge 重建流程
- 是否允许 Escape Pod 长期独立运行

这些问题应该等真实事故出现后，根据事故的实际需求决定。

## 未来真实事故时应该怎么开始

如果未来真的出现需要 Escape Pod 的事故，不应该再次从整个 Forge 开始考古。

首先重新检查本文记录的代码入口是否仍然存在：

- forge/runtime.py
- forge/tools/__init__.py
- forge/intents/executor.py
- forge/tools/intent_tools.py

然后确认当前版本是否仍然保持本文记录的层次关系。

之后只调查事故产生的增量问题。

例如：

- 如果只是某个 Forge Guard 阻止了正常执行，就不应该直接假设需要新的执行引擎。
- 如果是某个工具不可用，就只调查该工具及其依赖。
- 如果是 Runtime 本身失效，就重点确认 ToolExecutor、IntentExecutor、WorldRuntime 是否仍然可以独立使用。
- 如果是 World 或 Veritas 本身失效，则 Escape Pod 也不能假定自己可以绕过它。
- 如果是 Disk、Git、World 三者之间出现恢复问题，再单独调查 Forge 的同步与恢复机制。

原则是：

- 先确认事故破坏的是哪一层，再决定 Escape Pod 需要脱离哪一层。

不要因为“紧急”两个字，把整个 Forge 重新造一遍。

## 文档的使用方式

这份文档不是施工图。

它是一份考古成果。

未来如果 Escape Pod 永远没有真实需求，这份文档仍然保留今天已经完成的架构考察结果，并防止未来因为一时的紧急情绪而重新设计第二套 Forge。

如果未来真实事故发生，这份文档提供已经确认过的源码入口、执行层次和边界。

未来需要重新调查的是事故相关的变化，而不是今天已经确认过的基础问题。

如果 Forge 的源码结构发生重大变化，应当在 Escape Pod 真正施工前重新检查并更新本文档。

## 当前状态

Escape Pod 尚未实现。

目前没有真实事故证明需要实现它。

今天的代码考察已经完成其当前阶段的目的：

- 确认 Escape Pod 如果未来需要，可以建立在现有执行基础之上，而不是复制 Forge。

因此当前继续封存，不施工。

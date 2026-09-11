# Escape Pod：边界

## 为什么需要它

Forge 是一个有自身规则、治理流程和执行边界的运行环境。

正常情况下，任务应该在 Forge 内完成。

但存在一种极端情况：Forge 自身出现故障，而修复 Forge 所需要的执行能力又恰好受到 Forge 故障的影响。

这时，继续要求 Forge 自己完成修复，可能形成自我依赖。

因此，需要保留一种可能性：由用户主动启动一个位于 Forge 之外的独立执行体，在 Forge 无法可靠完成任务时执行外部任务或抢修。

这就是 Escape Pod 被提出的原因。

## 它不是什么

Escape Pod 不是第二个 Forge，也不是 Forge 的 emergency mode。

它不应该通过增加一个特殊模式、特殊权限或特殊状态，把一个本来位于 Forge 外部的执行体重新塞回 Forge。

它存在的意义，恰恰是当 Forge 本身成为问题的一部分时，仍然存在一个位于 Forge 外部的执行边界。

因此，它不继承 Forge 自身的治理流程。

这包括 Main AI 的判断边界、PendingAction、Human Intervention、SyncDecision 以及 Forge 的同步和任务治理流程。

## 它依赖什么

目前对 Forge 源码的检查已经确认，Forge 内部已经存在独立于 Main AI 治理层的执行基础。

语义工具可以通过 IntentExecutor 将操作交给 WorldRuntime，并由 Veritas 完成事务执行。

IntentExecutor 负责把 Intent 展开为 Veritas 原语，开启 World session，执行操作，在失败时 abort，在成功时 commit，并返回 Receipt 和 TransactionDelta。

IntentExecutor 本身不知道文件系统、Git 或 Forge 的同步治理。

因此，目前没有证据表明 Escape Pod 需要重新制造一套事务执行系统。

更合理的方向，是在真正需要它时复用已经存在的执行基础，而不是复制 Forge。

## 它不能绕过什么

不继承 Forge 的治理流程，并不意味着可以绕过更底层的世界事实。

Veritas 的事务、commit、abort 以及 WorldRuntime 的状态仍然属于执行本身。

Escape Pod 可以脱离 Forge 的治理，但不能因为脱离 Forge，就把一次没有成功提交的操作当成已经发生。

同样，World 状态、事务结果以及执行结果之间的区别仍然存在。

## 谁可以启动它

Escape Pod 的存在不是为了让 Forge 在需要时自行获得更高权限。

是否离开 Forge 的正常运行环境，应当由用户主动决定。

Forge 可以发现自己无法完成任务，也可以报告故障，但不能因为发现故障就自行启动 Escape Pod。

## 当前状态

Escape Pod 目前只是一个经过代码考察后形成的架构边界，不是已经实施的功能。

目前没有真实事故证明需要为它建立独立运行时、独立协议或独立治理系统。

因此现在不实现。

如果未来第一次真实事故证明 Forge 确实需要外部执行能力，再从真实事故反推它需要的最小能力。

不因为一个可能发生的极端情况，提前制造一套新的复杂系统。

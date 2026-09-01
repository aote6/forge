# Forge 待办

> 使用规则：
> - 固定分组：待解决；已解决条目直接删除
> - 条目字段：问题 + 发现场景 / 影响 / 建议 / 优先级
> - 修改待解决项时，优先更新现有条目；只有新问题才新增条目
> - 优先级：P0 架构 / P1 高频故障 / P2 观察语义 / P3 小改进

## 待解决

### 主 AI 决策权边界

- [ ] 明确 Main AI / Subagent / Gate / User 的决策边界。
  - 主 AI 默认负责理解任务、判断下一步和选择是否委托。
  - 简单读取、已有工具可直接完成的观察任务，不应无条件 spawn 子 AI。
  - 只有需要独立执行、并行探索、较大范围分析或其他明确理由时才委托子 AI。
  - 外部副作用、不可逆操作或需要用户裁决的事项，必须进入 Gate / Pending Action。
  - 已有充分证据时，主 AI 不应为了形式上的“再验证”机械增加子任务。
  - READ / WRITE 应依据实际行为判断，而不是仅依据工具名称。
  - 目标是从固定工作流恢复为由 Main AI 驱动的连续判断。
  - 优先级：P1

### 架构审计遗留

- [ ] Checkpoint clear 失败可能留下双文件。
  - 发现场景：Checkpoint / clear 过程中发生失败时，旧、新状态文件可能同时残留。
  - 影响：不直接导致 mutation，但可能造成恢复时状态歧义或残留。
  - 建议：审计 clear 的失败原子性与恢复选择规则，补充失败路径测试。
  - 优先级：P2

- [ ] `PAUSED` / `DISPATCHING` 状态枚举存在但不可恢复，文档契约需澄清。
  - 发现场景：RuntimeState 定义了生命周期状态，但进程重启后的恢复语义未完整闭环。
  - 影响：持久化状态可能落在无法继续恢复的阶段。
  - 建议：先明确这些状态是否允许持久化，以及重启后的合法恢复路径；确认后再决定代码是否需要修改。
  - 优先级：P2

- [ ] `sync_safety` 是 `WORLD_DISK_SYNC` 的收缩子集，需确认是否为预期设计。
  - 发现场景：审计发现两个同步安全语义的覆盖范围并不一致。
  - 影响：若非有意收缩，可能造成规范与实现之间的安全边界漂移。
  - 建议：对照现行 Normative 条款确认两者关系，不在结论确定前修改实现。
  - 优先级：P2

### 观察层 / 工具语义

### 测试技术债

- [ ] 旧测试门禁未迁移：`test_p2_3_progress_skeleton.py` 用 `monkeypatch.setattr(rtmod, "_WRITE_CONFIRM_TOOLS", frozenset())` 清空确认桶绕过 PendingAction 门禁。
  - 发现场景：Pending Action Gate 上线后，旧测试仍按「mutation 直接执行」假设编写，靠 monkeypatch 关闭门禁才通过。
  - 影响：这些测试没有验证真正的 PendingAction 契约，可能掩盖门禁回归。
  - 建议：逐步迁移为真实 PendingAction 流程（冻结 → 确认 → 执行），不再 monkeypatch 清空 `_WRITE_CONFIRM_TOOLS`。
  - 优先级：P2

### 主循环死代码清理

- [ ] 旧 PendingAction 死代码清理：主循环 `_pending_action` / `_execute_pending_action` / `_write_strategy` / `_WRITE_CONFIRM_TOOLS` 等已确认无活执行路径。
  - 发现场景：Phase 2 closure audit 确认主循环 schemas 只有 CONTROL_PLANE_TOOLS，WRITE_CONFIRM 分支永远不可达。
  - 影响：死代码增加维护负担，但当前不影响功能。
  - 建议：确认无测试依赖后清理，不碰子循环 confirm_fn / Execution Pause。
  - 优先级：P3

### 子 AI prompt 审查

- [ ] 子 AI system_prompt 审查：主 AI prompt 已改为控制层身份，但子 AI 的 SUBAGENT_SYSTEM 是否清晰表达「连续执行层」身份未审。
  - 发现场景：Phase 3 后主 AI 行为契约落地，子 AI prompt 未同步审查。
  - 影响：子 AI 可能保留旧工具指令或身份模糊。
  - 建议：审查 forge/subagent.py 里的 SUBAGENT_SYSTEM，确认其符合 Agent ABI v1.3 和 Execution Plane 定位。
  - 优先级：P2

### 系统集成能力

- [ ] Forge CLI 缺少单次机器调用入口（类似 claude -p），stdin 管道模式结束后 EOFError。
  - 发现场景：测试 `printf '说一句电影台词' | python3 dp.py` 时，Forge 能正常处理第一条消息并回复，但处理完回到交互循环继续读 stdin，管道已空，`read_multiline_input` 抛 EOFError。
  - 影响：Claude Code 或其他外部程序无法干净地用管道调 Forge。
  - 根因：dp.py 只有交互循环，没有「读一条消息 → 处理 → 干净退出」的单次入口。
  - 建议：给 dp.py 加 `-c / --command` 模式。
  - 优先级：P3

- [ ] Forge 无法主动使用 Termux 系统命令，必须用户明确指定完整命令。
  - 发现场景：用户说「Open URL」，主 AI 回复「我没有浏览器工具」。用户改说「Run: termux-open-url URL」后才执行成功。
  - 影响：用户必须知道底层命令名才能让 Forge 执行系统操作。
  - 建议：新增系统集成工具组（open_url / play_media / open_image 等）。
  - 优先级：P2

### 终端体验 / 产品可见性

- [ ] Forge 无法让用户直接看到终端动画/实时输出效果。
  - 发现场景：彩虹雨脚本运行成功，但用户只能看到静态 ASCII 摘要，看不到实时动画。
  - 影响：依赖 ANSI 颜色、光标移动、实时刷新的命令无法演示。
  - 建议：新增独立 PTY/交互终端能力，与 run_command 批处理捕获分离。
  - 优先级：P0

### 运行时生命周期（R1 后续）

- [ ] 控制面缺少 `list_recent_subtasks()` 工具。
  - 现状：_subagent_results 已持久化到 JSONL，但主 AI 无法主动回顾历史任务。
  - 影响：主 AI 无法回答「昨天派过什么任务、结果如何」。
  - 建议：新增控制面工具 list_recent_subtasks(limit=N)，返回最近 N 个 AgentResult 摘要。
  - 优先级：P3

- [ ] 全局停止/暂停/放弃机制未实现。
  - 现状：只有用户 q 或 Ctrl+C，无法只暂停子任务而保留主对话。
  - 影响：子 AI 长任务跑偏时只能中断整个进程。
  - 建议：基于 RuntimeState 设计全局信号（STOP / PAUSE / ABORT）。
  - 优先级：P2

### 主从分工 / 行为契约

- [ ] Forge 缺少「语义级风险提示」，只能在极危险命令上硬拦截。
  - 发现场景：删仓库、发垃圾嘟文、贴 API key 等语义上有害但技术可行的请求无法识别。
  - 建议方向：不做「AI 拒绝权」，做「风险提示 + 确认 + 极危险硬拦截」。
  - 优先级：P1（待深入研究）

- [ ] Forge 没有「代价预算」，主 AI 派发前不算成本。
  - 发现场景：简单任务被过度执行，子 AI 无限侦查。2026-09-01 发一条嘟文的任务中，子 AI 执行 20+ 次只读侦查才进入 post_toot。
  - 建议方向：AgentTask 估算成本；子循环加预算上限，超了返回 need_decision。
  - 优先级：P2

- [ ] 主 AI 判断本身没有被验证，它是最高裁判但没有更高一层查它。
  - 影响：主 AI 判断错误时没有任何机制阻止。
  - 建议方向：用户可用的 verify 或主 AI 判断审计日志。
  - 优先级：P2（架构哲学）

- [ ] 用户是最终裁决者，但用户没有独立验证工具。
  - 影响：用户只能信主 AI 的总结，或自己手动去查。
  - 建议方向：给用户一个验证主 AI 结论的通道。
  - 优先级：P2（架构哲学）

- [ ] 主 AI 和子 AI 都缺乏时间感知。
  - 影响：没有超时保护，长任务无限跑。
  - 建议方向：Runtime 注入轻量时间上下文，或子循环加超时预算。
  - 优先级：P3

- [ ] Forge 没有长期目标或工作记忆，重启后不会主动回顾昨天。
  - 影响：每次会话都从零开始。
  - 建议方向：主 AI 启动时自动回顾最近的 STATUS.md 和 TODO.md。
  - 优先级：P3（架构哲学）

- [ ] 用户意图缺少机器确认回显。
  - 发现场景：主 AI 理解偏差，用户要等任务跑完才察觉。
  - 建议：工程任务派发前，主 AI 必须先产出结构化确认，用户同意后才 spawn。
  - 优先级：P2

- [ ] 系统状态可观测性不足。
  - 影响：用户无法判断主 AI 是卡住了还是在思考。
  - 建议：状态栏展示 loop_turn、context_used、active_subtask。
  - 优先级：P3

- [ ] 主 AI 无事中监督通道，子 AI 跑偏时只能事后发现。
  - 影响：子 AI 的浪费性侦查无法被中途制止。
  - 建议方向：轻量监督信号或中途停止通道；需先裁定是否修改 AGENT_ABI §8。
  - 优先级：P2

### 行为验证

- [ ] CONFLICT 真实闭环验证未完成。
  - 现状：已验证 FAST_FORWARD 闭环，CONFLICT 场景方向不唯一，需要验证主 AI 列选项、用户选择、resolve、同步的完整链路。
  - 优先级：P2

- [ ] 行为验证扩展：已覆盖同步和测试套件，但「分析文件」「修 bug 并测试」「解释测试失败」等场景未验证。
  - 优先级：P2

### 文档治理

- [ ] WRI 与 Veritas Constitution 权威关系未裁定。
  - 现状：WRI 声称依赖 Veritas Constitution，但 Forge 仓库内无 Constitution 文本，无法确定谁高谁低。
  - 影响：升格 WRI 相关条款会卡住。
  - 建议：先取得或对齐 Constitution，再做 WRI 核心子集升格。
  - 优先级：P2

- [ ] 测试与 Normative 条款未正式绑定。
  - 现状：有大量测试，但没有「这条测试对应哪条 Normative 条款」的显式映射。
  - 影响：标准修订时不知道哪些测试需要跟着改。
  - 建议：为已升格的 sync_safety 建立测试映射，后续升格时一并补。
  - 优先级：P3

- [ ] 文档与代码漂移无自动监控。
  - 现状：全靠人肉审计，代码悄悄改行为文档不会自动发现。
  - 影响：文档和现实可能漂移。
  - 建议：定期审计或引入轻量一致性检查。
  - 优先级：P3

- [ ] 其余有资格升格的条款尚未正式升格。
  - 现状：AGENT_ABI 的 Evidence 绑定、HUMAN_INTERVENTION 的用户裁决、RUNTIME_STATE 的事实/水位分离，审计确认有升格资格但仍是 Contract 层级。
  - 建议：按 Promotion Policy 逐个升格，每次一个不变量包。
  - 优先级：P2


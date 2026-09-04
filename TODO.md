# Forge 待办

> 使用规则：
> - 固定分组：待解决；已解决条目直接删除
> - 条目字段：问题 + 发现场景 / 影响 / 建议 / 优先级
> - 修改待解决项时，优先更新现有条目；只有新问题才新增条目
> - 优先级：P0 架构 / P1 高频故障 / P2 观察语义 / P3 小改进

## 待解决

### 主 AI 只读后行为风险

- [ ] last 的提示时机、输入通道、显示对象与用户预期不一致。
  - 发现场景：
    1. 在写操作确认框中输入 last，会被当作非“确认”输入，从而等价于拒绝写入，而不是查看上一条完整输出。
    2. 工具运行过程中经常显示“输入 last 看全文”，但此时 Forge 正在同步执行 Runtime，用户实际上无法进入 forge> 输入 last。
    3. last 当前显示的是最近一次主 Runtime 工具输出的完整 display，不是子 AI 执行过程中的中间工具输出，因此用户可能误以为可以查看刚刚子 AI 的完整过程。
  - 影响：确认框误拒绝、提示时机误导、查看对象与用户预期不一致。
  - 建议：这是用户交互语义/行为一致性问题，先记录；不要为此设计新机制，不改 last 实现，不改 Runtime/subagent/CLI/测试。
  - 优先级：P2

- [ ] STOP 后子 AI 已产生的证据/上下文丢失，主 AI 无法说明“刚才子 AI 在做什么、已经查到了什么”。
  - 发现场景：2026-09-02 子 AI 连续执行工具时用户 Ctrl+C 软停成功，但 AgentResult 返回 blocked/user_stop，CONCLUSION empty、EVIDENCE 无；事后用户问主 AI“刚才在让子 AI 查什么”，主 AI 无法可靠回答。
  - 影响：用户中断后语义连续性断裂，主 AI 丢掉已经获取的事实。
  - 建议：user_stop 路径的 AgentResult 至少携带已执行工具摘要、已产生 evidence、原 AgentTask 摘要、停止原因；不做完整子任务持久化。
  - 优先级：P1

- [ ] 主 AI 可能把过度侦查从子 AI 转移到自己。
  - 发现场景：P1 给主 AI 开放 MAIN_READ_ONLY 后，主 AI 可能连续读取大量文件仍不形成判断，最后仍派宽泛任务。
  - 影响：token 浪费并未消失，只是从子循环转移到主循环。
  - 建议：观察主 AI 的实际读取次数与 spawn 质量；必要时为主 AI 只读阶段增加预算或提示约束。
  - 优先级：P2

- [ ] 主 AI 可能“读一点就以为懂了”，不再派子 AI 做深入验证。
  - 发现场景：主 AI 可以 read_file 后，可能只读一个函数或局部片段就下结论。
  - 影响：比完全瞎猜更危险，因为判断带有局部事实包装。
  - 建议：验证主 AI 在需要执行面验证时是否仍会 spawn；必要时在 prompt 或 AgentTask 构造层补强。
  - 优先级：P2

- [x] 主 AI 被 mutation policy 拒绝后，不会优雅转去 spawn_subagent。
  - 验证结果：2026-09-03 实机测试“种错字→修复错字”链路，主 AI 未尝试直接 mutation，而是主动 resolve_sync_decision 后派子 AI 执行写入；主从委托闭环成立。
  - 状态：已关闭。

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

- [ ] 旧测试门禁绕过点已迁移，但测试债未消失：`test_p2_3_progress_skeleton.py` 从清空 `_WRITE_CONFIRM_TOOLS` 改为 monkeypatch `_main_tool_policy_denied` 放行 `str_replace`。
  - 发现场景：P1 主 AI mutation policy 上线后，该测试为了让 mutation 成功路径可执行，又增加了一层 policy 绕过。
  - 影响：测试仍没有验证真实 PendingAction / 子任务 mutation 契约，只是绕过了新加的主 AI policy。
  - 建议：逐步迁移为真实 mutation 执行路径（子任务或内部合法执行入口），不再通过多层 monkeypatch 模拟 mutation 成功。
  - 优先级：P2

### 主循环死代码清理

- [ ] 旧 PendingAction 死代码清理：主循环 `_pending_action` / `_execute_pending_action` / `_write_strategy` / `_WRITE_CONFIRM_TOOLS` 等已确认无活执行路径。
  - 发现场景：Phase 2 closure audit 确认主循环 schemas 只有 CONTROL_PLANE_TOOLS，WRITE_CONFIRM 分支永远不可达。
  - 影响：死代码增加维护负担，但当前不影响功能。
  - 建议：确认无测试依赖后清理，不碰子循环 confirm_fn / Execution Pause。
  - 优先级：P3

### 子 AI prompt 审查

- [ ] 子 AI 执行层工具偏好不清晰：SUBAGENT_SYSTEM 未明确约束“只读优先专用工具、避免组合 shell”。
  - 发现场景：
    1. 2026-09-01 代码确认：SUBAGENT_SYSTEM 无「先计划再执行」约束，只有「不要无限搜索」软提示。
    2. 2026-09-02 实测：子 AI 连续使用 `pwd && ls -la`、`git ... && ls | head` 等组合命令做只读侦查，触发本可避免的确认。
    3. 2026-09-03 全链路验证：改一行 docstring，子 AI 多次用 `pwd && ls && head`、`cat ... | head` 等组合命令侦查，导致多次额外确认；最终全链路消耗 32 个工具。
  - 影响：专用只读工具被绕过；组合命令触发确认疲劳；审计和证据质量下降。
  - 建议：在 SUBAGENT_SYSTEM 中明确：
    - 只读侦查默认使用 read_file / glob_files / search_code / git_diff 等专用工具。
    - run_command 仅在专用工具无法覆盖时使用。
    - 禁止用 &&、|、; 等组合 shell 做日常侦查。
  - 优先级：P1


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

- [ ] 缺少独立 PAUSE / ABORT 语义，Ctrl+C 目前只能软停当前 turn。
  - 现状：已支持运行中 Ctrl+C 软停并回到 forge>；尚不支持只停子任务但主对话继续等待，或显式 ABORT 清理 pending。
  - 影响：子 AI 长任务跑偏时，只能软停整个当前 turn，不能单独暂停子任务后继续主对话。
  - 建议：基于现有 stop_requested 和 RuntimeState 增加轻量 PAUSE / ABORT 信号，不引入复杂状态机。
  - 优先级：P2

### 主从分工 / 行为契约

- [ ] Forge 缺少「语义级风险提示」，只能在极危险命令上硬拦截。
  - 发现场景：删仓库、发垃圾嘟文、贴 API key 等语义上有害但技术可行的请求无法识别。
  - 建议方向：不做「AI 拒绝权」，做「风险提示 + 确认 + 极危险硬拦截」。
  - 优先级：P1（待深入研究）

- [ ] FAST_FORWARD 方向唯一时，同步流程过度仪式化。
  - 发现场景：
    1. 2026-09-01 forge_sync 实际运行中，系统已知方向唯一，仍走 resolve_sync_decision → spawn_subagent → 确认 → forge_sync → verify 全套流程。
  - 影响：简单同步也变成多步状态机，用户和主 AI 都被流程拖着走。
  - 建议：方向唯一时允许主 AI 直接说明并请求确认，用户确认后走最短路径执行，不强制经过完整 decision + subagent 仪式。
  - 优先级：P2
  - 注：2026-09-03 已修复 payload.basis 与 summary 不一致 bug（stale PENDING 跨 basis 复用），主 AI 被迫侦查部分已消除。

- [ ] Forge 没有「代价预算」，主 AI 派发前不算成本，双循环都无真实工具调用预算。
  - 发现场景：简单任务被过度执行，子 AI 无限侦查。2026-09-01 发一条嘟文的任务中，子 AI 执行 20+ 次只读侦查才进入 post_toot。
  - 代码事实：max_steps 默认 15 且上限 15，计的是 LLM 回合数，不是工具调用总次数；同一回合可以执行多个工具调用，所以 max_steps 限制不住工具总数；主 AI 新增 MAIN_READ_ONLY 后，成本问题已从子循环扩展为主/子双循环。
  - 建议方向：AgentTask 估算成本；主/子循环都加工具调用总数或 token 预算，超了返回 need_decision 或暂停。
  - 优先级：P1

- [ ] 主 AI 判断本身没有被验证，它是最高裁判但没有更高一层查它。
  - 影响：主 AI 判断错误时没有任何机制阻止。
  - 进度：2026-09-03 已落地 MAIN_AUDITED_TOOL_NAMES，resolve_sync_decision / spawn_subagent 的控制面调用开始有审计记录。剩余缺口：其他判断（读什么、派什么任务）仍无验证通道。
  - 建议方向：用户可用的 verify 或主 AI 判断审计日志（已部分落地）。
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


### 行为验证

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




### sync_decision stale supersede 后 RuntimeState.pending 可能未持久化

- [ ] 实机发现：forge_sync 判 stale 后，sync_decision.json 正确写入新 PENDING，但 runtime_state.json 的 pending 仍是 null。supersede_decided_with_pending 里 rs_store.save(rs) 理论上执行了，但文件没变。需要实机复现并定位。
  - 优先级：P1
  - 影响：Gate 的 runtime_state 索引和 sync_decision 文件不一致，主 AI 看到 pending=null 但 resolve 又被 Gate 拦住

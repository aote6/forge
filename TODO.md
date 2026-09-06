# Forge 待办

> 使用规则：
> - 固定分组：待解决；已解决条目直接删除
> - 条目字段：问题 + 发现场景 / 影响 / 建议 / 优先级
> - 修改待解决项时，优先更新现有条目；只有新问题才新增条目
> - 优先级：P0 架构 / P1 高频故障 / P2 观察语义 / P3 小改进

## 待解决

### 主 AI 只读后行为风险

- [ ] WRITE_CONFIRM 确认框缺少只读检查命令通道
  - 现状：子任务写操作进入 cli_confirm 后，输入只能被解释为
    confirm/cancel。用户无法在确认暂停期间输入 last tc_xxx
    查看历史工具输出；任意非 confirm/cancel 输入都会返回
    False，并导致 user_denied_write。
  - 边界：
    - forge> 主 PendingAction 已可由 dp 先处理 last
    - human intervention 有独立的 continue/modify/abort 协议
    - 子 AI 内部工具输出已通过 emit + ToolCallRecord.display
      实现实时显示与历史 last tc_xxx，不属于未解决问题
  - 目标：确认框支持 side-effect-free inspection（至少 last /
    last tc_xxx），执行查看后保持原 WRITE_CONFIRM 状态，
    不改变 approve/deny 决策。
  - 优先级：P3

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

### 主从分工 / 行为契约
- [ ] 用户自然语言"停止"无法中断 run()/spawn_subagent（阻塞调用根因）
  - 发现：2026-09-02 用户在 forge> 打字"停止"，主/子 AI 工具
    循环不响应，只有 Ctrl+C 能杀。2026-09-06 代码审计确认：
    _stop_requested 只被 KeyboardInterrupt（Ctrl+C）和子任务
    user_stop 状态设置，没有任何"用户输入文本 → 设标志"的路径。
  - 根因：dp.py 的 forge> 提示符只在 runtime.run() 整个返回后
    才重新出现。run() 执行期间终端不在读 stdin，用户打字没有
    入口。这和 spawn_subagent 同步阻塞调用是同一根因——只要
    run()/spawn_subagent 是阻塞的，中途就没有代码路径接收新输入。
  - 边界：不是软停止机制缺失（_stop_requested 存在且被消费），
    是用户自然语言输入无法在阻塞期间被读到。
  - 方向：需要架构级改造——后台线程读 stdin + 设置 stop 标志，
    或 select/termios 非阻塞 stdin 监听，或把阻塞调用改为
    可中断检查点。不是加几行关键词判断能解决的 quick fix。
  - 优先级：P0（架构级，与"spawn_subagent 阻塞时主 AI 无法说话"
    合并处理）
- [ ] verify_evidence() 遗留函数清理（MDE v1 已隔离但未删除）
  - 现状：函数已标记 deprecated，无生产调用者。
    两个测试 tests/test_main_read_tool_records.py 仍引用它验证
    actor=main 过滤行为。
  - 风险：函数签名接受模型 items 并生成 Evidence，与 MDE 边界
    冲突。若未来有人误用其返回值进入 AgentResult.evidence，
    会重新打开"模型生产权威证据"的口子。
  - 修复方向：把两个测试迁移到 project_machine_evidence 的
    actor 过滤测试，然后删除 verify_evidence()。
  - 优先级：P1

- [ ] done_when 真正的语义求值（v1 只有 proxy）
  - 现状：done_when_satisfied_v1 是明确标注的 proxy：
    stop_when_met && machine_evidence >= 1。
    不读 done_when 自然语言内容，不判断任务是否真正完成。
  - 影响：主 AI 看到 status=done 时，实际只知道"子 AI 说停了
    且有成功工具调用"，不知道 done_when 是否真满足。
    主 AI 承担全部语义判断，但没有结构化支持。
  - 方向：设计结构化 completion predicate（如期望工具名+输出
    匹配），让机器能验证 done_when 的可观察部分。
  - 优先级：P1（独立设计，不混入 MDE）

- [ ] precheck acceptance semantics：证据链失败 ≠ 工程失败
  - 现状：precheck 在 done + 无 machine evidence 时 demote 为
    blocked。status_reason 已加文案说明"不代表工程失败"，但
    状态建模没变——主 AI 看到 blocked 仍无法区分"活没干好"
    和"证据链丢了"。
  - 影响：主 AI 可能误把验收失败当执行失败，违反 system prompt
    里"verify 失败 ≠ 工程任务失败"的规则。
  - 方向：考虑拆分 engineering outcome 和 evidence contract
    两个维度，或引入新的 status / 字段区分。
  - 优先级：P1（acceptance semantics 重构，另立任务）

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

- [ ] Forge 没有「代价预算」——子 AI 侧已修，主 AI 侧未修。
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


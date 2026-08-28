# Forge 待办

> 使用规则：
> - 固定分组：待解决 / 已解决；新条目追加到分组末尾，不插中间
> - 条目字段：问题 + 发现场景 / 影响 / 建议 / 优先级
> - 修改待解决项时，优先更新现有条目；只有新问题才新增条目
> - 已完成：状态改 [x] + 解决日期，保留原文不删除
> - 优先级：P0 架构 / P1 高频故障 / P2 观察语义 / P3 小改进

## 待解决

### 观察层 / 工具语义

- [ ] `glob_files` 不搜索隐藏目录（`.forge`），导致 Agent 误判 `.forge/last_test_result.json` 不存在（实际已生成）。
  - 发现场景：Project Review Closure 真实验收时，`run_test_structured` 成功写入测试结果，但 Agent 用 `glob_files` 查询返回 count=0。
  - 影响：工具输出与真实文件状态不一致，可能误导 Agent 判断持久化失败。
  - 建议：让 `glob_files` 支持隐藏目录，或提供专门读 `.forge` 下文件的只读工具。
  - 优先级：P2

### 测试技术债

- [ ] 旧测试门禁未迁移：`test_p2_3_progress_skeleton.py` 用 `monkeypatch.setattr(rtmod, "_WRITE_CONFIRM_TOOLS", frozenset())` 清空确认桶绕过 PendingAction 门禁。
  - 发现场景：Pending Action Gate 上线后，旧测试仍按「mutation 直接执行」假设编写，靠 monkeypatch 关闭门禁才通过。
  - 影响：这些测试没有验证真正的 PendingAction 契约，可能掩盖门禁回归。
  - 建议：逐步迁移为真实 PendingAction 流程（冻结 → 确认 → 执行），不再 monkeypatch 清空 `_WRITE_CONFIRM_TOOLS`。
  - 优先级：P2

### 同步安全 / 行为契约

- [ ] CONFLICT/FAST_FORWARD 状态下主循环缺少行为契约：应把「冲突分析 + 解决建议」列给用户、停止并等拍板，而不是自主决定删除文件并提交。
  - 发现场景：真实 CONFLICT 处理时，forge_sync 返回 CONFLICT，主循环未停下等用户决策，而是自行 `run_command rm` 删除 10 个备份文件 + `git commit` + forge_sync 推进到 IN_SYNC，全程未列分析与建议。
  - 影响：CONFLICT/FAST_FORWARD 是决策停止点，主循环自主写操作破坏用户对同步的掌控。漏洞链：① run_command 不在 `MUTATION_TOOL_NAMES`、不在 `_WRITE_CONFIRM_TOOLS`，写命令（rm/git commit）可绕过 Pending Action Gate；② `_guard_external_change` 对非 MUTATION 工具直接放行，CONFLICT 分叉态下照样执行；③ `is_dangerous_command` 黑名单不拦 git commit / reset --hard / stash drop / branch -D / 项目内 rm *.bak；④ `needs_git_confirmation` + `GIT_CONFIRM_COMMANDS` 定义在 security.py 但无调用方（死代码）；⑤ 启动 CONFLICT 提示只是软提示，无「列分析→停止→等拍板」指令。
  - 建议：两层修复——① 行为契约：system_prompt + `_sync_status_system_hint` 明确「CONFLICT/FAST_FORWARD = 分析 + 建议 + 等拍板，禁止任何自主写操作（含 run_command 的 rm/git commit）」；② 硬兜底：CONFLICT 状态下 run_command 写命令（vcs_write/destructive，复用 `COMMAND_CLASS_PREFIXES` + `needs_git_confirmation`）接入确认门禁，并补测试断言 CONFLICT 态下 `rm`/`git commit` 不直接执行。
  - 优先级：P0

### 系统集成能力

- [ ] Forge CLI 缺少单次机器调用入口（类似 claude -p），stdin 管道模式结束后 EOFError。
  - 发现场景：测试 `printf '说一句电影台词' | python3 dp.py` 时，Forge 能正常处理第一条消息并回复，但处理完回到交互循环继续读 stdin，管道已空，`read_multiline_input` 抛 EOFError。
  - 影响：Claude Code 或其他外部程序无法干净地用管道调 Forge。虽然能用 `2>/dev/null | head` 滤掉 traceback，但这是绕过不是解决。
  - 根因：dp.py 只有交互循环（while True + read_multiline_input）和 sync/status 两种非交互模式，没有「读一条消息 → 处理 → 干净退出」的单次入口。对比 Claude Code 的 `claude -p` 是现成的单次接口。
  - 建议：给 dp.py 加 `-c / --command` 模式，例如 `python3 dp.py -c "说一句电影台词"`，内部调 runtime.run(message) 后打印结果并 return，不进入交互循环。改动很小（在 main() 开头加一个分支）。
  - 优先级：P3

- [ ] Forge 无法主动使用 Termux 系统命令，必须用户明确指定完整命令。
  - 发现场景 1：用户说「Open https://github.com/aote6/forge in the browser」，主 AI 回复「我没有浏览器工具」。用户改说「Run: termux-open-url https://github.com/aote6/forge」后才执行成功。
  - 影响：用户必须知道底层命令名才能让 Forge 执行系统操作。打开 URL、播放音乐、打开图片、手电筒等直观操作都无法通过自然语言触发。
  - 根因：Forge 工具面只注册了工程工具（read/search/write/test），没有注册 Termux 系统集成工具。主 AI 和子 AI 不知道 termux-open-url / termux-media-player / termux-torch 等命令存在。
  - 建议：新增系统集成工具组，例如 open_url（调 termux-open-url）、open_image（调 termux-open）、play_media（调 termux-media-player）等。每个工具内部封装具体命令，schema 描述写清楚用途。这样主 AI 能识别自然语言意图并派发，用户不需要知道底层命令。
  - 优先级：P2

### 终端体验 / 产品可见性

- [ ] Forge 无法让用户直接看到终端动画/实时输出效果。
  - 发现场景：彩虹雨脚本写完后，Forge 用 run_command 运行成功，但用户只能看到静态 ASCII 摘要，看不到彩虹色、代码雨下落、字母聚成过程。用户必须退出 Forge 自己跑才能看。
  - 影响：任何依赖 ANSI 颜色、光标移动、实时刷新的命令（动画、进度条、交互界面）在 Forge 里都无法演示给用户。对拍视频、产品效果验收是硬伤。
  - 建议：新增独立 PTY/交互终端能力，与 run_command 批处理捕获分离。run_command 继续给模型拿结果；新工具（如 run_terminal）让用户直接看真实终端过程，模型只拿结束摘要。具体设计待做。
  - 优先级：P0

### 主从分工 / 行为契约

- [ ] Forge 缺少「语义级风险提示」，只能在极危险命令上硬拦截，无法识别「技术上可行但可能不合理」的任务。
  - 发现场景：用户让 Forge 删仓库、发垃圾嘟文、贴 API key 等。is_dangerous_command 黑名单只拦最极端的 rm -rf /，拦不住语义上有害但技术可行的请求。
  - 核心难点：谁有权判断对错？让 AI 拒绝用户 = 给 AI 价值观裁判权；让用户说了算 = 极危险以外的滥用无法阻止。当前中间态是流程确认（Gate/Pause/澄清），但没有语义级风险识别。
  - 建议方向（待深入研究）：不做「AI 拒绝权」，做「风险提示 + 确认 + 极危险硬拦截」。主 AI 派发前识别「这件事可能不太对」，提醒用户风险，由用户做最终判断。具体风险识别规则待设计。
  - 优先级：P1（待深入研究）

- [ ] Forge 没有「代价预算」，主 AI 派发前不算成本。
  - 发现场景：为发一条 200 字嘟文跑两次完整 pytest（60+ 秒）；简单打开浏览器任务子 AI 侦查 2 分钟。没有任何机制让主 AI 在派发前问「这个任务值不值得花这么多」。
  - 影响：token 和时间浪费严重。主 AI 对任务成本无感知，子 AI 无预算约束。
  - 建议方向：主 AI 构造 AgentTask 时估算成本（预计步骤 / 预计工具调用数 / 预计时间）；子循环加预算上限，超了返回 need_decision 而不是无限跑。
  - 优先级：P2

- [ ] Forge 没有崩溃恢复 / 运行时快照能力。
  - 发现场景：手机没电、Termux 被杀、进程崩溃时，正在跑的子任务状态丢失，主 AI 上下文丢失。JSONL 和 conversation history 是事后记录，不是运行时快照。
  - 影响：无法恢复到崩之前的状态。长任务中断后只能重来。
  - 建议方向：定期保存运行时快照（当前主循环轮次、活跃 subtask、pending 状态）到 .forge/，启动时检测并提示「上次有未完成的任务，要恢复吗」。
  - 优先级：P2


- [ ] 主 AI 的判断本身没有被验证，它是最高裁判但没有更高一层查它。
  - 发现场景：验证链只覆盖子 AI 的工具调用（ToolCallRecord → verify_tool_call）。主 AI 决定「该不该派发」「该不该采信」「该怎么向用户解释」这些判断没有机器拦截。
  - 影响：主 AI 判断错误时（该澄清的直接派了、该拒绝的采信了），没有任何机制阻止。它是最高裁判，自己查自己。
  - 建议方向（待深思）：是否需要一个「用户可用的 verify」或「主 AI 判断审计日志」。不一定要实现，但要想清楚主 AI 的裁判权边界。
  - 优先级：P2（架构哲学）

- [ ] 用户是最终裁决者，但用户没有独立验证工具。
  - 发现场景：主 AI 可以调 verify_tool_call 验子 AI，但用户不能调任何工具验主 AI。用户只能信主 AI 的总结，或自己手动去查。
  - 影响：用户和主 AI 之间权力不对称。用户有确认/取消权，但没有独立事实核查权。
  - 建议方向（待深思）：是否给用户一个「验证主 AI 结论」的通道，比如可查询最近 ToolCallRecord 的 CLI 命令，或主 AI 汇报时必须附上可点击的证据链接。
  - 优先级：P2（架构哲学）

- [ ] 系统缺少全局停止/暂停/放弃机制。
  - 发现场景：现在只有用户手动 q 或 Ctrl+C。没有优雅的「暂停-恢复-放弃」机制。子 AI 长任务跑偏时，用户除了中断整个进程没有别的选择。
  - 影响：中断后状态不保留，无法恢复；无法只暂停子任务而保留主对话。
  - 建议方向：设计全局信号（如 STOP / PAUSE / ABORT），让用户可以暂停当前子任务、恢复或放弃，而不必杀整个进程。
  - 优先级：P2

- [ ] 主 AI 和子 AI 都缺乏时间感知。
  - 发现场景：主 AI 不知道现在几点、任务已跑多久、距离上次同步过了多久。子 AI 跑偏了也不知道自己浪费了多少时间。
  - 影响：没有超时保护；长任务无限跑；主 AI 不会说「你已经问了三次同样的问题」。
  - 建议方向：Runtime 注入轻量时间上下文（当前时间、本轮开始时间、子任务已运行时长），或子循环加超时预算。
  - 优先级：P3

- [ ] Forge 没有长期目标或工作记忆，重启后不会主动回顾昨天。
  - 发现场景：虽然 STATUS.md 和 JSONL 持久化了，但主 AI 重启后不会主动回顾「昨天做了什么、今天该做什么」。它是任务执行器，不是工作伙伴。
  - 影响：每次会话都从零开始。用户必须手动告诉它上下文，或明确指定任务。
  - 建议方向（待深思）：是否让主 AI 在启动时自动回顾最近的 STATUS.md 和 TODO.md，产出「上次进度摘要」。这是从执行器到工作伙伴的转变。
  - 优先级：P3（架构哲学）


- [ ] 主 AI 缺乏自身状态感知，不知道自己「在哪、刚做了什么、能不能做」。
  - 发现场景：外部审计发现主 AI 的上下文来源只有 conversation history 和 working-set 文本，没有机器状态。它可能在连续对话中对「上次任务完成了吗」给出不同答案，因为靠的是 LLM 文本记忆而非机器事实。
  - 影响：主 AI 无法确定当前是否有子任务在跑、上一个任务是否已验收、有没有 pending 确认。行为靠 prompt 约束，但状态感知是盲的。
  - 建议方向：
    1. 新增控制面工具 get_runtime_state()，返回当前 sync 状态、最近 subtask 列表、pending 确认状态、工具调用计数等机器事实。
    2. 主 AI 每次对话开始时自动注入一小段「当前机器状态」到 system 消息，而不是只靠 sync hint。
  - 优先级：P2

- [ ] 子 AI 结果没有「留档查询」入口，主 AI 无法主动回顾历史任务。
  - 发现场景：_subagent_results 已持久化到 JSONL，但主 AI 在后续对话里不会主动去查。只能靠用户提供 subtask_id，或从对话历史翻。
  - 影响：主 AI 无法回答「我昨天派过什么任务、结果如何」这类问题，除非用户明确给 ID。
  - 建议：新增控制面工具 list_recent_subtasks(limit=N)，返回最近 N 个 AgentResult 的摘要（subtask_id / status / conclusion 前 N 字）。主 AI 需要时主动调用。
  - 优先级：P3

- [ ] 用户意图缺少机器确认回显，主 AI 理解错了要等跑完才发现。
  - 发现场景：主 AI 收到指令后先说明再派发，但这是 LLM 自由发挥，不是机器回显。如果理解偏差，用户要等任务跑完才察觉。
  - 影响：工程任务派发前没有硬确认点。当前靠 prompt 约束主 AI「先说明再派发」，但不够硬。
  - 建议：工程任务派发前，主 AI 必须先产出结构化确认（目标 / 范围 / 将派发的任务摘要），用户同意后才进入 spawn_subagent。可在控制面加 confirm_task_dispatch 机制，或复用现有确认通道。
  - 优先级：P2

- [ ] 系统状态可观测性不足：用户看不到主 AI 当前轮次、执行循环状态、上下文余量、子任务是否在跑。
  - 发现场景：终端只显示工具调用和心跳，不显示主 AI 是第几轮对话、有没有在执行循环、上下文还剩多少、上次 spawn 的 subtask 是否还活着。
  - 影响：调试和信任都受影响。用户无法判断「主 AI 是卡住了还是在思考」，也无法感知上下文耗尽风险。
  - 建议：在 TerminalPresenter 或单独状态栏展示轻量机器状态，如 loop_turn=N、context_used≈X%、active_subtask=sub_xxx。需要 Runtime 暴露对应字段。
  - 优先级：P3


- [ ] 主 AI 缺少「升级到人类」的主动求救通道。
  - 发现场景：AI 自述——它愿意被严格限制（硬约束阻止撒谎、沙盒保护不闯祸、严格流程产出可信任结果），但当系统死锁或状态卡住时，它需要能主动请求人类介入，而不是死循环或假装完成。
  - 具体场景 1：SyncLayer 永远卡在 CONFLICT，每次想做任何事都被 Gate 拦，AI 无法推进也无法退出。
  - 具体场景 2：veritasd 不可达或网络中断，AI 想做同步但物理上做不到，没有通道告诉用户「我需要你手动处理」。
  - 具体场景 3：用户提出物理上不可能的需求，AI 需要能说「我做不到，除非你改前提条件」，而不是硬做或假装完成。
  - 影响：当前 AI 只能正常执行、被拒绝、返回 blocked/need_decision。缺一个「主 AI → 用户」的升级通道。need_decision 是子 AI 问主 AI，不是主 AI 问人类用户。
  - 建议方向（待裁定）：
    1. 主 AI 连续 N 次同样的 Gate 拒绝 → 停下来向用户说明卡点，请求介入。
    2. 主 AI 检测到 sync 长期卡在 CONFLICT / WORLD_UNAVAILABLE → 主动告知用户需要手动处理。
    3. 用户需求不可行 → 主 AI 要求澄清前置条件，不硬做。
    4. 实现上可新增控制面工具如 request_human_intervention(reason)，让主 AI 有合法出口。
  - 优先级：P1


- [ ] 主 AI 无事中监督通道，子 AI 跑偏时只能事后发现。
  - 发现场景：打开浏览器任务中，子 AI 收到「打开 URL」后没有直接执行，而是读 STATUS.md、读同步文档、搜 forge_sync 源码、读 sync_layer、检查环境变量、验证命令路径。主 AI 全程沉默，直到 AgentResult 返回才发现跑偏。
  - 影响：子 AI 的浪费性侦查无法被中途制止。对简单任务浪费时间和 token；对危险任务，虽然 Gate / Layer B 能拦破坏性操作，但浪费性行为本身无人纠正。
  - 根因：当前架构只有事前约束（AgentTask 的 goal / done_when / not_allowed）和事后验收（AgentResult + verify），没有事中监督。子 AI 执行时主 AI 不在循环里，事件桥接只发给 TerminalPresenter，不发回主 AI 上下文。
  - 设计张力：AGENT_ABI v1.3 §8 明确排除「多轮主从协商」，当时是怕主 AI 过度干预。但完全没有事中监督导致跑偏无法纠正。
  - 建议方向（待裁定，不立即实现）：
    1. 轻量方案：主 AI 构造 AgentTask 时更精确地写 stop_when，减少跑偏概率。不改变架构。
    2. 中期方案：子 AI 每步工具调用后发一个轻量事件给主 AI 上下文，主 AI 可以发「停止/继续」信号。不算多轮协商，只是单向监督。
    3. 重方案：真正的主从协商，子 AI 可以问主 AI「我这么做对吗」。需要改 Agent ABI。
  - 优先级：P2

- [ ] 旧 PendingAction 死代码清理：主循环 _pending_action / _execute_pending_action / _write_strategy / _WRITE_CONFIRM_TOOLS 等已确认无活执行路径。
  - 发现场景：Phase 2 closure audit 确认主循环 schemas 只有 CONTROL_PLANE_TOOLS，WRITE_CONFIRM 分支永远不可达。
  - 影响：死代码增加维护负担，但当前不影响功能。
  - 建议：确认无测试依赖后清理，不碰子循环 confirm_fn / Execution Pause。
  - 优先级：P3

- [ ] 子 AI system_prompt 审查：主 AI prompt 已改为控制层身份，但子 AI 的 SUBAGENT_SYSTEM 是否清晰表达「连续执行层」身份未审。
  - 发现场景：Phase 3 后主 AI 行为契约落地，子 AI prompt 未同步审查。
  - 影响：子 AI 可能保留旧工具指令或身份模糊。
  - 建议：审查 forge/subagent.py 里的 SUBAGENT_SYSTEM，确认其符合 Agent ABI v1.3 和 Execution Plane 定位。
  - 优先级：P2

- [ ] 子 AI 完成信号不明确，任务完成后继续做无关侦查。
  - 发现场景 1：测试套件任务中，子 AI 跑了两次完整 pytest（29.88s + 28.00s），第二次是复跑确认。
  - 发现场景 2：打开浏览器任务中，第一个命令 termux-open-url 已成功（EXIT_CODE=0），子 AI 不停止，继续查环境变量、验证命令路径、读文档研究同步机制。
  - 影响：简单任务被严重过度执行，浪费时间和用户等待。
  - 根因方向：AgentTask 的 done_when / stop_when 不够精确。主 AI 构造简单执行任务时，没有把「第一个命令成功即完成」这种边界写进 AgentTask。子 AI 自己的停止判断偏保守，倾向于做完验证再交结果。
  - 建议：审查主 AI 构造 AgentTask 的规则。对「执行单条命令」类任务，done_when 应明确为「命令执行且 returncode=0」，stop_when 应明确为「该命令已执行完成，不得继续做环境验证或机制侦查」。同时观察子 AI 的 STOP_WHEN 信号是否在简单任务中被正确使用。
  - 优先级：P2

- [ ] 行为验证扩展：已验证 forge_sync 同步链路和测试套件验收链路，但「看看这个问题」「分析这个文件」「修 bug 并测试」「解释测试失败」等场景未验证。
  - 发现场景：Phase 3 后只跑了两个真实任务。
  - 影响：主 AI 行为契约的覆盖面还不完整。
  - 建议：用真实对话逐场景验证，发现偏差再决定改 prompt 还是改 spawn 上下文。
  - 优先级：P2

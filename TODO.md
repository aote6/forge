# Forge 待办

> 使用规则：
> - 固定分组：待解决；已解决条目直接删除
> - 条目字段：问题 + 发现场景 / 影响 / 建议 / 优先级
> - 修改待解决项时，优先更新现有条目；只有新问题才新增条目
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

- [ ] durable pause / 子循环恢复未实现。
  - 现状：R1 最小闭环已持久化 phase + active_subtask_id + pending，但子循环栈内状态不可恢复。
  - 影响：子 AI 执行中崩溃后无法从断点继续，只能重新 spawn。
  - 建议方向：先定义 durable pending 的最小形态，再决定是否持久化子循环消息和 PC。
  - 优先级：P2

- [ ] 控制面缺少 `get_runtime_state()` 工具。
  - 现状：RuntimeState 已持久化，但主 AI 无法通过工具查询当前 phase / pending / active_subtask。
  - 影响：主 AI 状态感知仍靠对话历史和 sync hint，没有机器事实入口。
  - 建议：新增控制面只读工具 get_runtime_state()，返回 RuntimeState 摘要。
  - 优先级：P2

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

- [ ] 主 AI 缺少「升级到人类」的主动求救通道。
  - 发现场景：AI 死锁或状态卡住时，无法主动请求人类介入。
  - 影响：只能正常执行、被拒绝、返回 blocked/need_decision，没有主 AI → 用户的升级通道。
  - 建议：新增控制面工具 request_human_intervention(reason)。
  - 优先级：P1

### 主从分工 / 行为契约

- [ ] Forge 缺少「语义级风险提示」，只能在极危险命令上硬拦截。
  - 发现场景：删仓库、发垃圾嘟文、贴 API key 等语义上有害但技术可行的请求无法识别。
  - 建议方向：不做「AI 拒绝权」，做「风险提示 + 确认 + 极危险硬拦截」。
  - 优先级：P1（待深入研究）

- [ ] Forge 没有「代价预算」，主 AI 派发前不算成本。
  - 发现场景：简单任务被过度执行，子 AI 无限侦查。
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

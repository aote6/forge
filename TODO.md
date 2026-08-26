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

### 终端体验 / 产品可见性

- [ ] Forge 无法让用户直接看到终端动画/实时输出效果。
  - 发现场景：彩虹雨脚本写完后，Forge 用 run_command 运行成功，但用户只能看到静态 ASCII 摘要，看不到彩虹色、代码雨下落、字母聚成过程。用户必须退出 Forge 自己跑才能看。
  - 影响：任何依赖 ANSI 颜色、光标移动、实时刷新的命令（动画、进度条、交互界面）在 Forge 里都无法演示给用户。对拍视频、产品效果验收是硬伤。
  - 建议：新增独立 PTY/交互终端能力，与 run_command 批处理捕获分离。run_command 继续给模型拿结果；新工具（如 run_terminal）让用户直接看真实终端过程，模型只拿结束摘要。具体设计待做。
  - 优先级：P0

### 主从分工 / 行为契约
- [ ] 主 AI 默认还是自己干活，没有把复杂执行任务派给子 AI。
  - 发现场景：Agent ABI v1 六步完成后，启动 Forge 处理 CONFLICT，主 AI 自己用 run_command 分析、删除、提交，没有一次使用 spawn_subagent。主 AI 监督子 AI 的能力已建成，但主 AI 没有被强制使用。
  - 影响：Agent ABI 的监督通道形同虚设。子 AI 的边界修好了，但主 AI 默认行为不变，用户看到的还是主 AI 自己从头干到尾。
  - 建议：在 system_prompt 里把 spawn_subagent 从“可选探索工具”改为“默认执行路径”。明确主 AI 职责收窄为判断、派工、验收；读多文件探索、跨目录定位、小范围修改并回报等任务必须派子 AI。主 AI 自己直接做工程活应该是例外，不是常态。
  - 优先级：P0

## 已解决

### 工具可见性

- [x] post_toot 在 Planning 阶段不可见，导致模型绕路用 run_command 手动发帖。
  - 解决：Pending Action Gate 让所有工具始终可见，post_toot 不再被 Planning schema 隐藏（2026-08-26）。
  - 优先级：P1 ✅

### 工具分类设计

- [x] 工具分类三层 + 两阶段（Planning / Execution）规则过多，模型容易绕路。
  - 解决：Pending Action Gate 替换 Phase 状态机；权限轴简化为 READ / WRITE；forge_sync 独立 FORGE_SYNC 策略（2026-08-26）。
  - 测试：test_pending_action_gate.py + test_forge_sync_gate.py；全量 510 passed。
  - 优先级：P0 ✅


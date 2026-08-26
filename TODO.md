# Forge 待办

## 观察层 / 工具语义

- [ ] `glob_files` 不搜索隐藏目录（`.forge`），导致 Agent 误判 `.forge/last_test_result.json` 不存在（实际已生成）。
  - 发现场景：Project Review Closure 真实验收时，`run_test_structured` 成功写入测试结果，但 Agent 用 `glob_files` 查询返回 count=0。
  - 影响：工具输出与真实文件状态不一致，可能误导 Agent 判断持久化失败。
  - 建议：让 `glob_files` 支持隐藏目录，或提供专门读 `.forge` 下文件的只读工具。
  - 优先级：P2（不影响核心功能，但违反观察语义一致性）

## 工具可见性

- [ ] post_toot 在 Planning 阶段不可见（在 MUTATION_TOOL_DECLARATIONS），导致模型发嘟文时绕路用 run_command 手动调用 MastodonClient。
  - 发现场景：用户要求发英文嘟文，模型说「当前会话没有直接暴露该工具」，然后 search_code + read_file + run_command 手动发帖。
  - 影响：工具调用数 9 次，违背工具描述「直接调用本工具，无需搜索实现或手写脚本」。
  - 建议：考虑将 post_toot 移入 RECONCILIATION 或新建 ACTION_TOOL_DECLARATIONS（用户明确指令可直接执行的副作用操作），让 Planning 阶段可见。
  - 优先级：P1（用户高频操作被破坏）

## 工具分类设计待议（明天解决）——已解决（2026-08-26）

- [x] 当前工具分类三层（READ_ONLY / RECONCILIATION / MUTATION）+ 两阶段（Planning / Execution）规则过多，模型容易绕路。
  - 解决：Pending Action Gate 替换 Phase 状态机；工具始终完整可见，权限轴简化为 READ / WRITE；确认 = 冻结精确 tool_call 快照，不再整段计划。
  - forge_sync 独立 FORGE_SYNC 策略：detect 只读观察；仅 FAST_FORWARD 才冻结 PendingAction 确认推进；CONFLICT STOP。
  - 关联关闭：post_toot Planning 阶段不可见（P1，见上）随「工具始终可见」一并解决；RECONCILIATION 分类由 FORGE_SYNC 策略取代。
  - 测试：test_pending_action_gate.py + test_forge_sync_gate.py；全量 510 passed。
  - 优先级：P0 ✅

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

## 工具分类设计待议（明天解决）

- [ ] 当前工具分类三层（READ_ONLY / RECONCILIATION / MUTATION）+ 两阶段（Planning / Execution）规则过多，模型容易绕路。
  - 发现场景：发嘟文时模型在 Planning 阶段看不到 post_toot，绕路用 run_command；forge_sync 因「安全对账」被单独分类，但规则不统一。
  - 待议：是否简化为两层（READ / WRITE），所有非只读操作统一走「计划 → 确认 → 执行」；forge_sync 是否也应先确认再执行。
  - 关联：post_toot Planning 阶段不可见（P1）与 forge_sync RECONCILIATION 分类是否保留，一并解决。
  - 优先级：P0（设计层面，影响后续所有工具分类）

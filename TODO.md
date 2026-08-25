# Forge 待办

## 观察层 / 工具语义

- [ ] `glob_files` 不搜索隐藏目录（`.forge`），导致 Agent 误判 `.forge/last_test_result.json` 不存在（实际已生成）。
  - 发现场景：Project Review Closure 真实验收时，`run_test_structured` 成功写入测试结果，但 Agent 用 `glob_files` 查询返回 count=0。
  - 影响：工具输出与真实文件状态不一致，可能误导 Agent 判断持久化失败。
  - 建议：让 `glob_files` 支持隐藏目录，或提供专门读 `.forge` 下文件的只读工具。
  - 优先级：P2（不影响核心功能，但违反观察语义一致性）

## 规划/执行切换

- [ ] 模型在 Planning 阶段说「提交执行计划」但未调用 submit_plan 工具，导致卡住；用户需手动说「执行」才进入 Execution 阶段。
  - 发现场景：删除 apply_20260825a.py 时，模型确认了计划但未触发 submit_plan。
  - 影响：多一轮用户交互，降低流畅度。
  - 建议：检查 submit_plan 在 Planning 阶段的 schema 描述是否足够明确，或 system prompt 是否需要强调「计划确认后必须调用 submit_plan」。
  - 优先级：P2

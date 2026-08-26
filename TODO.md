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

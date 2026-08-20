# Forge 状态

## 精修轮（第三轮：架构澄清 + P0清单复核）
- system_prompt.py: write_file 覆盖已存在文件时加前置提示（不拦截，仅提示"若只改部分内容建议用str_replace"）
- STOP_HINT 熔断消息加失败原因分类（type_mismatch/exception/logic），并附带针对性建议文案
- 手动验证熔断机制：连续3次失败后第4次准确拦截，reason分类正确显示

## 架构澄清（重要，写给下轮/其他AI实例看）
- 原始P0清单"TaskIntent/IntentType命名冲突"、"world-state vs code操作误分类硬校验器"
  两项，排查后确认：这两个问题依附的旧架构（Planner/plan_validator.py 独立分类层）
  已在 59fa405 重构中被完全删除。新架构下模型直接选择具体工具
  （write_file/create_object/...），不存在独立的"分类判断"步骤会出错。
  **结论：这两项P0问题本身已随架构变更失效，不需要继续排查旧代码。**
- "机器判定已定义符号事实标注"：查了 edit_contract.py 的 ensure_machine_ops，
  确认这是行编辑操作的 authoring/machine 两种schema转换契约，跟"符号是否已定义"
  语义无关，是查错方向。真正相关的是 symbols_from_edit（已存在，但只在编辑后
  记录，不是编辑前判断）。如果仍需要"编辑前"的符号存在性提示，需要新写，
  不是在 edit_contract.py 里找。
- "重试prompt携带结构化拒绝原因"：现有 STOP_HINT 已从纯计数升级为
  reason分类（type_mismatch/exception/logic），基本满足原始诉求。

## 已知技术债（未处理）
- 全库47处裸 `except Exception:`（历史遗留为主），建议分批清理
- ~~`forge: tx=NN v=NN` 自动commit与人类feature commit混线~~ 已解决：
  自动事务提交已停用（git_projection.apply() 不再 commit），历史中的
  `forge: tx=` 提交已通过 filter-branch 全部移除
- veritas_kernel 侧：object_birth 收窄 pub(crate)、WAL截断恢复测试，完全未碰
- forge/intents/intent_tools.py:210 投影结果被静默丢弃：ProjectionManager.project()
  内部对每个 projection 的 apply() 包 try/except（base.py:89-121），失败不抛异常、
  返回 success=False 的 ProjectionResult；调用处未检查返回值，_register_path 照常执行，
  可能返回"世界里存在但磁盘无文件"的 oid（2026-08 只读确认，未改）
- forge/projections/base.py:122-133 永不执行的旧实现死代码（121 行已 return，未删）

## 生产路径不变

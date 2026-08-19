# Forge 状态

## 生产路径

Runtime.run → 工具循环（MAX 40 步）→ IntentExecutor → Veritas → Projection

## 工具质量收敛（本轮）

- LLM schema 从 ~40 压到 ~25；实现层仍注册旧工具以兼容测试
- 新增首选编辑：`str_replace`（精确串替换）、`write_file`（整文件）
- 新增 `glob_files`
- system_prompt 改为「str_replace 优先」决策树
- 步数上限 20 → 40

## 仍保留的差异化

Veritas 事务 / Receipt / World 对象链接 — 编辑对外像 Code，对内仍走事务。

# Forge 状态

## 精修轮（本轮 + AI审计跟进）
- str_replace/write_file/modify_file 熔断签名统一为内容感知（path+内容hash），避免同路径改内容被误判重复调用
- symbols_from_edit 改为优先返回真正变化的符号（对称差），未变符号垫后，避免 max_n 截断丢失关键新增符号
- session_changes 持久化改为追加写 jsonl（原全量重写是 O(n²)），summary 做换行清洗
- undo_last_tx 两处裸吞异常改为 stderr 日志，便于长会话排查
- _compress_messages 按工具类型分层：确认型工具（write_file/modify_file/undo_last_tx等）仍压成一行；
  内容型工具（read_file/str_replace/diff等）保留多行+800字符预算，避免第20步后模型"以为看过"实则内容已被压没
- read_files（批量读）接入 read_cache，此前只有单文件 read_file 命中缓存，批量路径完全绕开
- system_prompt.py 工具简写对齐 schemas.py 真实工具名（glob→glob_files, search→search_code）

## 已知技术债（未处理，供下轮参考）
- 全库 47 处裸 `except Exception:`（历史遗留为主），建议分批清理而非一次性
- goal_clarify 调用点外层裹了裸 except，模块失效时无可观测性
- `forge: tx=NN v=NN` 自动commit与人类feature commit混在主线历史，
  可用 `git log --grep="^forge: tx=" --invert-grep` 过滤；是否挪到独立ref待定
- todo_write 未纳入 _CONFIRMATION_TOOLS，走默认多行压缩分支，非错误但浪费上下文预算

## 生产路径不变

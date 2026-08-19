# Forge 状态

## 精修轮（本轮）

- str_replace 熔断签名: path + old_string 哈希（换 old 可重试）
- COVERAGE_HINT 使用编辑触及的 def/class 符号
- projection 摘要: world=/disk= 明确半成功
- undo_last_tx: 清读缓存、记 session_changes、标明 shadow 与 World 可能滞后
- dp.py: `last`/`copy` 打印上一工具块；`changes` 列本会话修改

## 生产路径不变

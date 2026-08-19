# Forge 状态

## 本轮：Agent 痛点全量修复

| 优先级 | 项 | 实现 |
|--------|----|------|
| P0 | 相关测试 | mutation 成功后 RELATED_TESTS + 建议 target |
| P0 | veritasd 错误 | errors.classify → VERITAS offline 人话 |
| P0 | 读缓存+失效 | read_cache mtime；mutation 后 invalidate |
| P1 | 上下文压缩 | 旧 tool 结果压成一行，保留最近 6 条 |
| P1 | todo 提醒 | 步数过半注入未完成 todo（用户消息优先） |
| P1 | NEAR_MISS | str_replace 失败附近似片段 |
| P2 | memory | mutation 更新 last_status + recent_files |
| P2 | subagent evidence | 结论格式 path/line/reason/evidence |

## 生产路径不变

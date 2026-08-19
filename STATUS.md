# Forge 状态

## 本轮（Subagent + UX）

| 项 | 状态 |
|----|------|
| spawn_subagent | 独立工具循环，只读+str_replace/write_file，返回结论 |
| 失败恢复建议 | str_replace / write_file / run_command / link 等 fail 文案增强 |
| run_command 截断 | 保留末尾（错误常在最后） |
| str_replace 空白容错 | strip 首尾空白后再匹配 |
| read_file 长文件 | >200 行默认前 100 + 后 50 |
| 会话摘要 | q 退出保存；启动注入 |
| 世界摘要 | dp.py 启动打印一行 |
| 工具统计 | 任务结束打印 tools=N |
| require_confirm | Intent 默认 False |
| run_legacy / confirmation / agent_state | DEPRECATED |

## 生产路径不变

Runtime.run → 工具循环 → IntentExecutor → Veritas → Projection

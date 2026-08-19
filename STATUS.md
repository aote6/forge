# Forge 状态

## 工程化体验（本轮）

| 项 | 说明 |
|----|------|
| P0 自动 DIFF | str_replace / write_file / apply_patch 成功后附 unified diff |
| P1 测试定位 | run_test_structured → failure_context + source 窗口 |
| P2 veritasd 检查 | dp.py 启动非阻塞探测 |
| P3 install.sh | Python≥3.10、找 veritasd、pytest、bin/forge |
| P4 历史持久化 | conversation_history.json + session_summary 注入 |
| P5 返回格式 | 高频工具 RESULT:/FAILED: 前缀 |

## 生产路径

Runtime → 工具循环 → IntentExecutor → Veritas → Projection

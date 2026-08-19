# Forge 状态

## 产品体验闭环（本轮）

1. **自动登记**：str_replace/write_file/modify 对磁盘已有文件无 ObjectId 时，自动 create_file 登记并更新 ObjectPathMap
2. **TodoWrite**：内存任务列表，复杂任务先拆步
3. **apply_patch**：unified diff → 单事务多文件
4. **NEXT 提示**：写成功后的 display 附带建议 run_test / git_diff
5. **web_fetch**：urllib 抓取 http(s) 文本

## 生产路径

Runtime → 工具循环 → IntentExecutor → Veritas → Projection（不变）

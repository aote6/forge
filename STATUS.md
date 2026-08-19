# Forge 状态

## 本轮（敢干活 UX）

- format_block 统一手机/多 AI 复制块
- undo_last_tx（.forge/tx_shadow 文件级撤销，深度 5）
- read_file >150 行 → outline
- mutation 返回 BEFORE/AFTER/DIFF + CLIP
- run_command ERROR_SLICES
- project_memory.json + 启动注入 system

## 生产路径不变

工具循环 → IntentExecutor → Veritas → Projection

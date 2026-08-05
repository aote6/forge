# Forge 项目状态

## 当前能力
- 工具：read_file / search_code / prepare_write / commit_write / cancel_write / run_command
- 双模型支持：`gg`（Gemini）/ `dp`（DeepSeek），均可传项目路径：`dp ~/veritas_kernel`，不传默认整个家目录
- 安全层：forge/core/security.py（路径黑名单 + 命令黑名单），操作日志见 .forge/operation_log.jsonl

## 已知限制（未解决，非阻塞）
- 黑名单式防护，非白名单，只挡已知危险模式，防不住未知组合
- run_command 立即执行，无二次确认（git push/rm 等已在黑名单里，但仍建议高危操作前肉眼复核）
- 无 prompt injection 防护，模型读到的文件内容里若含诱导性文本存在被诱导执行风险
- prepare_write 目前是单文件粒度事务，无跨文件原子提交

## 下一步可选项（未做）
- git/gh 类命令走确认流程而非立即执行
- 白名单模式作为更严格的可选运行方式

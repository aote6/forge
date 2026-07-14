# Forge

Transactional Software Engineering Runtime for LLMs.

AI 代码助手框架，支持 DeepSeek / Gemini。
核心能力：读取文件 → 准备修改 → diff 预览 → 用户确认 → 事务提交 → 自动备份 → 语法校验 → 失败回滚。

## 快速开始

export DEEPSEEK_API_KEY="你的key"
alias dp='cd ~ && python ~/.forge/gg.py'
dp

## 架构

adapters/ — 模型适配器（DeepSeek/Gemini）
core/ — 文件管理、Patch引擎、事务、备份、校验
tools/ — AI 可调用的工具函数
runtime.py — 事件驱动主循环
workspace.py — 统一操作入口

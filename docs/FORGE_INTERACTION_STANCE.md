# Forge 产品裁定：用户交互模型

状态：冻结
前置文档：
- FORGE_IDENTITY.md
- FORGE_PRODUCT_STANCE.md
- FORGE_WORKSPACE_STANCE.md
- FORGE_VERITAS_STANCE.md

本文件只裁定 Forge 与用户之间的交互模型。
不讨论具体 UI、TUI 布局、命令格式或前端实现。

## 一、目标驱动

Forge 的交互模型是目标驱动的。

用户向 Forge 表达希望达成的目标，
而不是通过配置项或固定命令来驱动 Forge 的核心工作。

自然语言是当前核心交互方式，
但目标驱动原则本身不绑定某一种输入形式。

## 二、用户表达目标，Forge 执行并返回结果

用户不需要直接管理 Forge 内部的执行过程。

Forge 根据目标进行相应工作，
并向用户反馈执行结果。

## 三、不同载体可以有不同前端，但交互本质统一

CLI、TUI、GUI、Web、移动端或其他前端
都可以有自己的表现形式。

但都应遵循同一个核心模型：

用户表达目标

→ Forge 执行

→ 返回结果

## 四、Claude Code 式终端 UI

Claude Code 式终端 UI 可以是 Forge 当前最合适的前端之一，
但不构成 Forge 的身份。

终端交互界面是 Forge 的一种前端实现，
不是 Forge 本体，也不是唯一交互形式。

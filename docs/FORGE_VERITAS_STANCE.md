# Forge 产品裁定：Forge 与 Veritas 的关系

状态：冻结
前置文档：
- FORGE_IDENTITY.md
- FORGE_PRODUCT_STANCE.md
- FORGE_WORKSPACE_STANCE.md

本文件只裁定 Forge 与 Veritas 在产品层面的关系。
不讨论连接协议、WRI、veritasd 或具体实现方式。

## 一、Veritas 不是 Forge 的启动条件

Forge 接入可用 AI 后即可进入正常工作状态。

没有 Veritas 不意味着 Forge 功能被降级。

无 Veritas 时，Forge 处于正常独立工作形态。

## 二、连接 Veritas 是用户主动选择

Forge 不要求用户安装、启动或连接 Veritas。

用户需要时主动连接即可。

连接行为不属于 Forge 的最小启动路径。

## 三、连接 Veritas 后，Forge 身份不变

连接 Veritas 后，Forge 可以使用 Veritas 提供的机器能力，
并遵循 Veritas 的运行规则。

但 Forge 仍然是 Forge，Veritas 仍然是独立机器。

这是工作环境的变化，不是 Forge 身份的变化。

## 四、两种正常工作形态

Forge
│
├── 无 Veritas
│   └── 正常工作
│
└── 连接 Veritas
    ├── 正常工作
    ├── 使用机器能力
    └── 遵循机器规则

不使用以下措辞描述无 Veritas 状态：

- 降级模式
- fallback
- reduced mode

无 Veritas 是正常形态之一，不是残缺状态。

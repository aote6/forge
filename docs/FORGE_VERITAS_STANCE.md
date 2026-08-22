# Forge 产品裁定：Forge 与 Veritas 的关系

状态：冻结
前置文档：
- FORGE_IDENTITY.md
- FORGE_PRODUCT_STANCE.md
- FORGE_WORKSPACE_STANCE.md

本文件只裁定 Forge 与 Veritas 在产品层面的关系。
不讨论连接协议、WRI、veritasd 或具体实现方式。

## 一、Veritas 不是 Forge 的启动条件

Forge 接入可用 AI 后即可进入可工作状态。

Veritas 不参与 Forge 的启动。

## 二、Forge 有两种正常工作形态

### 无 Veritas：只读工作形态

Forge 可以：

- 查看
- 分析
- 交互
- 规划

但不能进行写操作：

- 不能创建
- 不能修改
- 不能删除

无 Veritas 不是残缺状态，而是 Forge 的只读工作形态。

### 有 Veritas：可变更工作形态

Forge 在只读能力之外，
获得完整的工程变更能力。

写操作必须通过 Veritas 事务，
形成可信变更闭环。

## 三、Veritas 的产品定位

Veritas 不是 Forge 的启动依赖。

Veritas 是 Forge 获得可信变更能力的机器。

用户第一次打开 Forge 时：

- 可以读
- 可以看
- 可以分析
- 可以讨论

用户需要让 Forge 真正修改东西时：

连接 Veritas。

连接后，Forge 才拥有改变世界的能力。

## 四、两种形态关系

Forge + AI
│
├── 无 Veritas
│   ├── 查看 / 分析 / 交互 / 规划
│   └── 不可修改
│
└── 有 Veritas
    ├── 查看 / 分析 / 交互 / 规划
    ├── 创建 / 修改 / 删除
    └── Veritas Transaction → Commit

不使用以下措辞描述无 Veritas 状态：

- 降级模式
- fallback
- reduced mode
- 残缺品

无 Veritas 是只读工作形态。
有 Veritas 是可变更工作形态。

两者都是 Forge 的正常形态。

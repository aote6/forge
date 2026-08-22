# Forge 与 Veritas：当前实现下的关系

状态：有效（按实现事实描述，不是权限禁令）
前置：FORGE_IDENTITY.md

本文件说明 **当前代码路径** 下，有无 Veritas 时 Forge 实际通哪些能力。
不讨论「禁止用户改文件」——源码可见，用户随时可以自己改盘或改 Forge。
这里只回答：主路径里，不装 / 不启 veritasd 时，什么好用、什么会失败。

## 一、启动

Veritas **不是**启动条件。

接入可用 AI 后即可对话与跑工具循环。找不到 veritasd 不妨碍安装和启动。

## 二、无 Veritas 时实际可用的

当前实现里，不依赖 veritasd 的包括：

- 读文件、搜索、glob、符号与 repo 地图
- 对话、分析、规划（含 submit_plan 确认流）
- 一般 shell（`run_command` 等，仅受既有危险命令黑名单约束）
- 跑测试、看 git diff 等本地能力
- 会话与 `.forge` 运行态

也就是说：无 Veritas 时，Forge 仍是一个能读代码、能搜、能跑命令、能规划的工程 Agent 壳。

## 三、无 Veritas 时主路径上不可用的

当前实现把 **工程写入主路径** 绑在 Veritas 事务上：

`str_replace` / `write_file` / `create_file` / `modify_file` / `delete_file` / batch / patch  
→ IntentExecutor → WorldSession → veritasd → Receipt → Projection

以及 World 对象、链接、`forge_sync`、基于事务的撤销等。

veritasd 不在线时，上述调用在事务层失败，**没有**平行的「官方无 Veritas 写盘主路径」。

这不是产品在「禁止写」，而是：**可信变更闭环这条链当前只接到了 Veritas。**

## 四、旁路（事实，不是承诺）

- 模型仍可能调用 `run_command` 等方式改工作区；那不经 Veritas，也没有 Receipt。
- 用户不用 Forge、直接改文件或改 Forge 源码，与本文件无关。

旁路存在 ≠ 产品保证无 Veritas 也能走事务写入。

## 五、有 Veritas 时多出来的

连接并跑通 veritasd 后，主路径上的写入走事务，有 Receipt，再投影到磁盘——这是当前实现提供的 **可信变更** 能力，也是相对「直接写盘」类工具的差异所在。

## 六、一句话

| 条件 | 实际含义 |
|------|----------|
| 无 Veritas | 探索 / 规划 / shell 等可用；**事务性工程写入主路径不可用** |
| 有 Veritas | 在以上之外，主路径写入可走 Veritas 事务与证据链 |

不使用「降级 / 残缺 / 禁止写」描述无 Veritas；只描述能力通断。

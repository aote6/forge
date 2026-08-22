# Forge

Forge 是一个目标驱动的软件工程 Agent：接入可用 AI 后即可工作。

它有两种正常工作形态：

| 形态 | 条件 | 能力 |
|------|------|------|
| **只读工作形态** | 已接入 AI，未连接 Veritas | 查看、分析、交互、规划 |
| **可变更工作形态** | 已接入 AI，并连接 Veritas | 在只读能力之上，可创建 / 修改 / 删除；写操作经 Veritas 事务，带 Receipt 证据 |

Veritas 不是 Forge 的启动条件；它是进入可变更工作形态、获得可信变更闭环的机器。

无 Veritas 不是残缺或降级，而是只读工作形态。有 Veritas 时，修改先在事务中完成，commit 后有 Receipt，再投影到文件系统——改错了可依据世界状态恢复，而不是只能依赖 git 碰运气。

## 为什么存在

常见代码助手直接写磁盘，改错了主要靠 git 回退。

Forge 把「能否改用户工作区」明确成工作形态边界：需要变更时连接 Veritas，让每次写入走事务与证据链；不需要变更时，仍可只读分析与规划。

## 它是什么

Forge 是工具循环 Agent：LLM 逐步调用工具，根据真实结果再决定下一步。

典型能力包括：读文件、搜索代码、跑测试、任务拆解、子任务委派、联网查文档；在可变更形态下还包括字符串替换、写文件，以及世界对象与链接等（均经 Veritas 事务，而非直接写盘）。

交互模型是目标驱动的：用户表达目标 → Forge 执行 → 返回结果。终端 UI 是当前合适的前端之一，不是唯一形态。

## 安装

```bash
./install.sh
export DEEPSEEK_API_KEY=...   # 或其他已配置的 AI 接入
python3 dp.py                 # 或 PATH 中的 forge
```

- 需要可用的 AI 才能进入可工作状态。
- **不要求**安装 Veritas 才能安装、启动 Forge；无 Veritas 时进入只读工作形态。
- 需要创建 / 修改 / 删除时，再安装并启动 `veritasd`，进入可变更工作形态。

## 使用

在提示符输入目标，例如：

```text
帮我找一下 str_replace 失败的原因，返回文件和行号
```

```text
把 test_echo.py 里的 hello forge 改成 hello gemini
```

（后一类目标在可变更形态下才会真正写入；只读形态下可分析与规划，不能改工作区。）

## 和 Code 类工具的关系

工具循环与常见 Agent 同类。差异在变更边界：可变更形态下 Forge 走 Veritas 事务与 Receipt；Code 类工具通常直接写磁盘。事务、世界对象、链接关系是连接 Veritas 后的能力，不是「没有 Veritas 就不叫 Forge」。

## 测试

```bash
python3 -m pytest -q
```

## 更多

- 产品裁定：`docs/FORGE_IDENTITY.md` 及同目录 `FORGE_*_STANCE.md`
- 工程状态：`STATUS.md`
- 架构与恢复等：`docs/`

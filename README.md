# Forge

Forge 是一个运行在 Veritas 确定性世界内核之上的工程 Agent。

它不是普通的代码助手。所有修改都经过 Veritas 事务，每次提交都有 Receipt 证据，改错了可以撤销，世界状态可以恢复。

## 为什么存在

Claude Code 和 Cursor 直接写磁盘，改错了只能靠 git 回退。

Forge 在 LLM 和磁盘之间放了一层 Veritas 世界。每次修改先在事务里完成，commit 后有 Receipt，然后才投影到文件系统。

这样你得到的不是一堆改了不知道改没改好的文件，而是一个有版本、有证据、能撤销的世界状态。

## 它是什么

Forge 是一个工具循环 Agent。LLM 逐步调用工具，每步看到真实结果后再决定下一步。工具包括读文件、字符串替换、跑测试、搜索代码、创建世界对象、建立链接、任务清单、子任务委派、联网查文档。

写代码的主路径和 Claude Code 一样：找文件、读代码、字符串替换、跑测试、看 diff。但每步修改内部走 Veritas 事务，不是直接写盘。

## 安装

运行 install.sh 检查环境。设置 DEEPSEEK_API_KEY。执行 python3 dp.py 进入交互。

## 使用

在提示符输入任务，例如：

创建一个新的 World 对象并 link 到 id=1

把 test_echo.py 里的 hello forge 改成 hello gemini

帮我找一下 str_replace 失败的原因，返回文件和行号

## 和 Code 的关系

工具循环机制和 Code 同类。区别在突变路径：Code 直接写磁盘，Forge 走 Veritas 事务。Code 有的 Forge 基本都有，Forge 有的事务、Receipt、世界对象、链接关系 Code 没有。

## 测试

python3 -m pytest -q

## 更多

看 STATUS.md 了解最近改动和当前状态。看 docs 目录了解架构设计。

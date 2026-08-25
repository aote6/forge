# Forge Security Boundary

```
Type: Security / Product Boundary
Authority: Binding
Status: Active
Scope: Forge trust boundaries and capability safety semantics
```


前置：`FORGE_IDENTITY.md`、`FORGE_PRODUCT_STANCE.md`

本文只固定**安全边界语义**：什么是当前攻击面、什么是本地信任边界、什么是 Safety Guard、什么必须等远程能力接入前再建立。  
不是完整审计报告，不承诺绝对安全，不替代代码实现。

---

## 1. 目的

回答三件事：

1. Forge **当前**对谁暴露了什么执行能力  
2. 哪些机制是**硬边界**，哪些只是**辅助防护**  
3. 未来若加入远程入口，**必须先满足**哪些条件  

不讨论 CVSS、不设计完整权限系统、不重审代码。

---

## 2. 当前攻击面

Forge 当前入口是**本地 CLI**（`dp.py` REPL）。

当前**没有**：

- HTTP 控制面  
- Web UI  
- REST API  
- WebSocket / TCP 监听控制面  
- 远程 RPC 控制面  

与本地 `veritasd` 的通信是：

```text
本地子进程 · stdin/stdout · JSON Lines
```

不是网络监听服务。

因此：

> 当前不存在 DSH 类「未授权远程 Agent 控制面 / 远程 RCE」攻击面。

这**不等于**「Forge 没有安全风险」。本地 Agent 仍可在用户会话内执行命令、读写工作区；风险在本地信任边界内，不在未授权远程控制面。

---

## 3. 本地执行能力与信任边界

`run_command`（及同类本地 shell / 测试执行）是 **本地 Agent 执行能力**。

模型是：

```text
本地用户主动启动 Forge
  → 用户授权 Agent 执行项目任务
  → Agent 可调用 run_command 等工具
  → 以当前本地用户权限执行
```

因此：

> `run_command` 落在**本地用户信任边界**内：用户已把 Agent 跑在自己的机器与工作区上。

它**不是**当前的「未授权远程 RCE」。

同时必须明确：

> shell 执行本身仍有风险（误操作、恶意项目内容诱导、黑名单绕过等）。  
> 「本地用户主动启动」≠ 绝对安全。

阶段模型（规划 / 确认 / 执行）主要约束**文件 mutation**；`run_command` 在只读 schema 中可用，**阶段不是 shell 的硬安全边界**。

---

## 4. Safety Guard 与 Security Boundary

这是本文最关键的区分。

### Safety Guard（辅助防护）

包括但不限于：

- `is_dangerous_command` 与命令黑名单  
- 路径黑名单 / workspace 逃逸检查  
- 工具输出 sanitizer / secret 脱敏  

用于：

- 降低明显危险操作概率  
- 拦截已知危险模式  
- 减少 Agent 误操作  
- 额外风险控制  

**不是**完备隔离：

- 黑名单不承诺覆盖全部 shell 语义或全部绕过  
- 不依赖「模型是否听话」作为唯一防线  
- **不能单独**构成对远程调用者的权限隔离  

### Security Boundary（硬安全边界）

必须由**系统结构**强制保证，例如：

```text
远程调用者  ×  无法获得本地 shell capability
```

这类限制**不能**依赖：

- LLM 是否听话  
- Prompt 文案  
- 黑名单是否写全  
- 模型是否生成危险字符串  

> **Safety Guard 可以降低风险，但不能单独构成 Security Boundary。**

当前远程硬边界的现实情况是：**没有远程控制面**——入口仅限本地 CLI。本地 shell 能力仍在用户信任边界内，由 Guard 辅助，而非「对不可信远程调用者的隔离」。

---

## 5. Prompt Injection 与不可信上下文

Forge 会把下列内容送入模型上下文，例如：

- 项目文件、README、源代码  
- 测试输出、工具输出  

因此存在路径：

```text
不可信内容 → Agent Context → 影响模型行为 → 可能调用已有工具
```

这是 **Prompt Injection 风险**，凡带工具的 LLM Agent 共有，Forge 未额外开放远程控制面并不消除该路径。

已有 sanitizer / redact 用于：

- 降低不可信**工具输出**污染后续上下文的风险  
- 标记部分指令性句式  
- 脱敏 secret  

> sanitizer 是**软缓解**，不是 Prompt Injection 的硬防御；**不**宣称「已解决 Prompt Injection」。

---

## 6. FATAL / DEGRADED / WARN

工具与运行态结果语义必须区分，禁止把所有异常都叫 WARN。

| 语义 | 含义 | 约定 |
|------|------|------|
| **FATAL** | 主操作未完成 | `success = False`；操作失败 |
| **DEGRADED** | 主操作可能已成功，但关键状态无法完全确认或不再可信 | 必须**可观测且被消费**（guard / 后续逻辑读取）；禁止只设无人读取的标志 |
| **WARN** | 附属操作失败；主操作仍成功 | 只提示风险，**不**改变主操作成功语义 |

DEGRADED 示例方向：path_map 不可信时禁止继续文件 mutation，直到恢复——标志必须进入真实拦截路径，而不是仅日志。

---

## 7. 未来远程能力的前置安全条件

以下**不是**当前漏洞，而是接入 **A2A / HTTP API / Web UI / 远程 Agent 调用** 之前必须满足的条件。

1. **远程身份认证**  
   远程调用者必须经过认证。

2. **Local Tool Schema 与 Remote Tool Schema 分离**  
   本地用户可用的工具集合 ≠ 远程调用者自动可用的集合。

3. **`run_command` 默认不对远程开放**  
   远程请求不得仅因「连上 Forge」就获得本地 shell；`run_command` 默认从 Remote Tool Schema 排除。

4. **远程 Session 不自动继承本地权限**  
   - 不自动获得本地 shell  
   - 不能通过参数自行声明更高权限  
   - 必须在系统侧绑定明确的调用者身份与 capability  

未满足上述条件前，不得将现有本地 tool surface 直接暴露为远程控制面。

---

## 8. 边界总结

| 命题 | 结论 |
|------|------|
| 当前是否有未授权远程 Agent 控制面 | **否**（仅本地 CLI） |
| `run_command` 是什么 | 本地用户信任边界内的执行能力，**不是**当前未授权远程 RCE |
| 黑名单 / `is_dangerous_command` | **Safety Guard**，不是完备 Security Boundary |
| sanitizer | 软缓解；**不**等于已解决 Prompt Injection |
| 阶段模型（规划/执行） | 主要约束文件 mutation；**不是** shell 硬边界 |
| 未来远程入口 | 必须先具备认证、Schema 分离、`run_command` 默认不对远程开放、Session 不自动继承本地权限 |

本文档只固定语义；实现变更以代码为准。安全相关代码改动后，应复核本文是否仍与实现一致。

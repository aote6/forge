# Forge 架构文档

> 状态：2026-08-23，基于当前代码（dp.py / forge/runtime.py / forge/intents/ / forge/world/ / forge/projections/ / forge/sync/ / forge/core/ / forge/adapters/）。
> 标注约定：**[FACT]** = 代码可观测事实；**[HYPOTHESIS]** = 推断（未经直接验证）。

## 1. 总览图（ASCII）

```
                    ┌──────────────────────────────────────────────┐
                    │        dp.py  (CLI / REPL 入口)               │
                    │  交互输入 · 工具输出渲染 · 健康自检 · sync      │
                    └───────────────┬──────────────────────────────┘
                                    │  Runtime.run(task)
                                    ▼
┌────────────────────────────────────────────────────────────────────┐
│                    Runtime  (forge/runtime.py)                     │
│  ┌────────────┐ ┌────────────┐ ┌────────────┐ ┌────────────────┐   │
│  │ adapter    │ │ workspace  │ │ memory     │ │ conversation   │   │
│  │ (BaseAdap) │ │ (Workspace)│ │(MemoryStore)│ │ (msg log)      │   │
│  └────────────┘ └────────────┘ └────────────┘ └────────────────┘   │
│  ┌────────────┐ ┌────────────┐ ┌──────────────────────────────┐    │
│  │ world      │ │ projections│ │ sync_layer + sync_state       │    │
│  │(WorldRuntime│ │(ProjectionMgr)│ │ (World↔Disk 三态对账)        │    │
│  └────────────┘ └────────────┘ └──────────────────────────────┘    │
│  守卫: _guard_external_change · _guard_pending_verify · 事件钩子     │
└───────────────┬────────────────────────────────────────────────────┘
                │ tools 字典
                ▼
┌────────────────────────────────────────────────────────────────────┐
│       ToolExecutor  —— 调度 + 连续失败熔断(STOP_HINT)               │
└───────────────┬────────────────────────────────────────────────────┘
                │
    ┌───────────┴──────────┬──────────────────┬──────────────────┐
    ▼                       ▼                  ▼                  ▼
┌──────────────────┐  ┌──────────────────┐ ┌────────────────┐ ┌──────────────┐
│ 只读工具族        │  │ 变更工具族         │ │ 世界工具        │ │ 元/自省工具   │
│ read/search/     │  │ intent_tools     │ │ world_tools    │ │ meta/display │
│ test/git_tools   │  │ (make_intent_    │ │ (纯 World 操作) │ │ session_     │
│ related_tests    │  │  tools)          │ └──────┬─────────┘ │ changes/     │
└────────┬─────────┘  └────────┬─────────┘        │           │ project_     │
         │                     │                  │           │ memory/      │
         ▼                     ▼                  ▼           │ subagent     │
    Workspace/disk        IntentExecutor     WorldSession    └──────┬──────┘
    (直接读/写)            (forge/intents/    (forge/world/        │ 子Agent循环
        │                 executor.py)       session.py)           │ (run_subagent)
        │                     │                  │                 │
        │                     │  直接盘回退       │ create_object/   │
        │                     ▼   direct_disk    │ freeze/link…     │
        │              ┌──────────────────┐      ▼                  │
        │              │  edit_contract    │  WorldAdapter ──► veritasd ──► WAL/账本
        │              │  authoring↔machine│   (RPC + durable WAL) │
        │              │  ops 校验/转换    │      ▲                │
        │              └────────┬─────────┘      │                │
        │                       │                │ receipt 历史    │
        ▼                       ▼                │                │
┌──────────────────────────────────────────────────────────────────┐ │
│              ProjectionManager (forge/projections/)               │ │
│   FileProjection ── 磁盘文件 ↔ World 对象 (ObjectPathMap)          │ │
│   GitProjection  ── git HEAD/状态                                 │ │
│   IndexProjection ── .forge 索引                                 │ │
└───────────────────────────────┬──────────────────────────────────┘ │
                                │                                    │
                                ▼                                    │
┌──────────────────────────────────────────────────────────────────┐ │
│               SyncLayer (forge/sync/)                             │◄┘
│   detect(): IN_SYNC / FAST_FORWARD_* / CONFLICT /                 │
│             NOT_A_GIT_REPO / WORLD_UNAVAILABLE                    │
│   sync(): 安全推进 · 冲突则 STOP（不自动覆盖）                       │
│   SyncState → .forge/sync_state.json（水位，不放进 Veritas）         │
│   RecoveryCheck (forge/recovery/) → 启动同步自检/对账               │
└───────────────────────────────┬──────────────────────────────────┘
                                │
        ┌───────────────────────┼───────────────────────┐
        ▼                       ▼                       ▼
┌───────────────┐      ┌─────────────────┐      ┌──────────────────┐
│ Disk / Git    │      │ veritasd 守护进程 │      │ LLM 后端           │
│ (工作区文件)    │      │ (对象账本+WAL)    │      │ DeepSeek /        │
│               │      │                 │      │ OpenAI-compat /   │
│  ──磁盘侧状态──  │      │  ──世界侧真相──   │      │ Mastodon(纯HTTP)  │
└───────────────┘      └─────────────────┘      └──────────────────┘
```

## 2. 关键数据流

### 2.1 只读路径
```
用户 → Runtime → ToolExecutor → read/search/test/git 工具 → Workspace/disk → 回显给用户
```
[FACT] 只读工具直接访问磁盘/工作区，不经过 World。

### 2.2 变更路径
```
用户 → Runtime → ToolExecutor → intent_tools → IntentExecutor
    → WorldSession → WorldAdapter → veritasd (commit/abort)
    → 投影回写磁盘 (FileProjection) + SyncLayer 水位推进
    → receipt 入账（veritasd 侧）
```
[FACT] 文件内容变更统一走 World 事务；磁盘只是投影。

### 2.3 veritasd 不可达时降级
```
veritasd 不可达 → world_available=False
    → 文件 mutation 走 direct_disk 直写（有 WAL 兜底）
    → 纯 World 操作（create_object / freeze / link）硬失败，不伪装成直写
```
[FACT] 冷启动/守护进程缺失时不崩溃，能力按可达性降级；Mastodon 工具豁免 World 可达性检查（env+HTTP）。

### 2.4 forge sync / status 对账
```
forge sync|status → SyncLayer.detect()
    → IN_SYNC（无操作）
    → FAST_FORWARD_*（沿明确方向安全推进）
    → CONFLICT（停止，展示 diff，等待用户决策）
    → NOT_A_GIT_REPO / WORLD_UNAVAILABLE（错误态）
```
[FACT] 启动/外部修改检测后优先对账；冲突绝不自动覆盖。

## 3. 设计要点

| 要点 | 说明 | 依据 |
|---|---|---|
| 单一工具循环 | Runtime 一次性注册全部工具（只读+变更+世界+元自省）到 ToolExecutor，无独立 planning 循环；变更工具内部自管事务 | [FACT] |
| World 是唯一真相 | 内容 mutation 全部经 IntentExecutor → WorldSession → veritasd；磁盘是投影，SyncLayer 对账，冲突 STOP | [FACT] |
| 同步元数据不入 Veritas | `.forge/sync_state.json` 是权威水位，防同步状态与业务状态相互污染 | [FACT]（STATUS.md 决策 1） |
| 冷启动降级 | Identity 建立失败只降级不崩溃；Mastodon 工具豁免 World 可达性检查 | [FACT]（P2-1） |
| detect() 缓存 | SyncLayer.detect() 用「磁盘指纹 + 同步水位」缓存，命中返回浅拷贝，不再每次全量扫 receipt 历史 | [FACT]（P3-4） |
| 工具自动事务 | 变更工具组（intent_tools）自带事务语义，失败回滚/收尾 | [HYPOTHESIS]（依赖 tool 内部实现，未逐一定点验证） |

## 4. 安全边界（核心不变量）

```
⚠️ Disk ≠ World → STOP. No auto-overwrite. World is truth.
```
- 冲突时 SyncLayer 绝不自动覆盖磁盘或 World。
- direct_disk 仅覆盖「文件内容」类变更；纯 World 操作不降级。
- 同步水位存于 `.forge/sync_state.json`，不写入 Veritas 账本。

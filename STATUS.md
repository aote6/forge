# Forge 状态

## 早期历史摘要（7-14 ~ 8-20，从 Git 历史提炼）

> 注：本段为 2026-08-25 从 Git 历史恢复的早期脉络摘要。完整逐日记录曾因整理失误从工作区删除，但全部 commit 仍在 Git 历史中（`git log --follow STATUS.md` 可查）。以下保留关键节点，不逐字恢复流水账。

### 起点（7-14）
- Forge v0.2 起步：事务式 AI 代码助手（prepare_write → commit_write / cancel_write），仅 DeepSeek 单模型。

### 安全层建立（8-05）
- 路径黑名单 + 命令黑名单 + Git 确认机制 + Prompt Injection 清洗器（sanitizer）
- 双模型（gg / dp）；VeritasClient 从 subprocess 升级为 veritasd 常驻进程（stdin/stdout JSON Lines）
- WorldRuntime v1 落地：Forge 成为 Veritas 世界上的系统软件

### Projection / Intent / Recovery 体系（8-06）
- Phase 1~5 连续落地：Projection 框架 → Intent 层 → FileProjection → GitProjection / IndexProjection
- P8~P10：ObjectPathMap、Projection 幂等保护、Checkpoint 持久化、崩溃恢复端到端
- Recovery Constitution v1.0 诞生：Projection 恢复不变量正式成文
- 架构裁决：删除 tx_ids，Checkpoint 只保存 version

### Engineering Loop（8-06 晚）
- Forge v2-alpha：Planner + 完整闭环，Engineering Loop 自动串联
- Lu Patch Engine 接入：create/modify/delete 统一走 Lu

### 架构收敛（8-07）
- v1 architecture closure：唯一 Runtime、VERIFY→PLAN、无 Hub fallback、modify strict
- P0 Runtime Closure：消灭第二写路径
- P1-A/B/C：单事务、Checkpoint 绑 Veritas version、VERIFY 语义闭合

### 规划系统建设（8-08）
- P1~P8：版本化仓库快照、符号引用索引（Python AST）、失败分类自纠错、规划精度、验证可靠性、Plan Obligation 覆盖、测试目标选择
- 203 passed 全绿

### 关键 bug 修复（8-09）
- intent-based routing、malformed-edit guard、checkpoint 隔离
- capability_grants 持久化：自持 AdminCap 随 receipt 重放跨重启存活
- 删除死代码 veritas_adapter.py

### 语义修复（8-12 ~ 8-13）
- P5：stage 预览不泄露 + modify 语义修正（0-indexed 半开区间）+ delete 后 ObjectPathMap 清理
- CapabilityGrant 薄适配

### 身份与决策边界（8-16 ~ 8-17）
- Identity Binding Boundary Audit：发现身份绑定缺口
- Forge 角色定位文档、Veritas 自举锚点
- Edit Contract Closure：唯一 authoring→machine 转换边界
- CREATE_OBJECT 语义穿透全链路闭合
- Planner P0 收口：机器观测符号事实、fail-closed 边界、规划反模式标记
- 机器与 LLM 决策权边界正式沉淀（PLANNER_DECISION_BOUNDARY）

---

## recovery 分叉问题（今天发现，已修复 120eee2）
- 问题：启动 recovery 重放 receipt 时无条件用 World 内容覆盖磁盘，
  会冲掉用户手动修改的文件（数据丢失风险）。
- 修复：FileProjection 新增 `recovery_preserve_disk`，recovery 期间开启。
  磁盘文件已存在且内容 ≠ World → 跳过覆盖、保留磁盘版本；缺失文件仍从
  World 恢复；删除操作同样跳过；仍推进 checkpoint 避免反复重放。
- 测试：tests/test_recovery_preserve_disk.py 4 用例（preserve/restore/idempotent/legacy），173 passed。
- 遗留语义：分叉后 World 与磁盘持久不一致，需用户手动选择以哪边为准
  （recovery 会打印分叉路径与指引）。

## 精修轮（第三轮：架构澄清 + P0清单复核）
- system_prompt.py: write_file 覆盖已存在文件时加前置提示（不拦截，仅提示"若只改部分内容建议用str_replace"）
- STOP_HINT 熔断消息加失败原因分类（type_mismatch/exception/logic），并附带针对性建议文案
- 手动验证熔断机制：连续3次失败后第4次准确拦截，reason分类正确显示

## 架构澄清（重要，写给下轮/其他AI实例看）
- 原始P0清单"TaskIntent/IntentType命名冲突"、"world-state vs code操作误分类硬校验器"
  两项，排查后确认：这两个问题依附的旧架构（Planner/plan_validator.py 独立分类层）
  已在 59fa405 重构中被完全删除。新架构下模型直接选择具体工具
  （write_file/create_object/...），不存在独立的"分类判断"步骤会出错。
  **结论：这两项P0问题本身已随架构变更失效，不需要继续排查旧代码。**
- "机器判定已定义符号事实标注"：查了 edit_contract.py 的 ensure_machine_ops，
  确认这是行编辑操作的 authoring/machine 两种schema转换契约，跟"符号是否已定义"
  语义无关，是查错方向。真正相关的是 symbols_from_edit（已存在，但只在编辑后
  记录，不是编辑前判断）。如果仍需要"编辑前"的符号存在性提示，需要新写，
  不是在 edit_contract.py 里找。
- "重试prompt携带结构化拒绝原因"：现有 STOP_HINT 已从纯计数升级为
  reason分类（type_mismatch/exception/logic），基本满足原始诉求。

## P3-1 第一批：裸 except Exception 清理（2026-08-23）

只清第一批 10 处纯 `pass`（完全静默），不碰 P3-2~P3-6，不推远程，未 commit。

### 清理的 10 处（`except Exception: pass` → stderr 日志）
1. `forge/tui_input.py:295` — 关闭 bracketed-paste 终端转义失败
2. `forge/adapters/mastodon.py:109` — 读取限流状态 JSON 失败
3. `forge/adapters/mastodon.py:123` — 写入限流状态失败
4. `forge/intents/executor.py:52` — 事务 abort 失败（原异常仍 re-raise）
5. `forge/tools/local_tools.py:123` — read_files cache_put 失败
6. `forge/tools/local_tools.py:802` — summarize_file 读取缓存失败
7. `forge/tools/local_tools.py:899` — read_file cache_put 失败
8. `forge/tools/local_tools.py:1152` — run_command 学习 test_command 失败
9. `forge/world/runtime.py:211` — close() 时 session abort 失败
10. `forge/world/adapter.py:127` — close() 时 terminate/kill 子进程失败

每处只加 `print(..., file=sys.stderr)`，不动控制流、不改返回语义、不抛新异常。

### 结果
- 纯 `pass`（完全静默）：12 → 2
- 剩余 2 处：`checkpoint.py:51` / `sync/state.py:65`（`.broken` 重命名失败，主 load 失败已打日志，属冗余兜底，留待后续）
- 全量测试：395 passed，10 skipped

## P3-1 第二批：静默默认值清理（2026-08-23）

只清第二批 12 处 `return ""` / `return None` / `continue`（无日志），不碰 P3-2~P3-6，不推远程，未 commit。

### 清理的 12 处（`except Exception` 静默默认值 → 加 stderr 日志）
1. `forge/runtime.py:563` `_load_session_summary` — `return ""`
2. `forge/runtime.py:618` `_load_task_state` — `return None`（顺带把 docstring「静默」措辞改为「损坏则记日志」）
3. `forge/runtime.py:900` `_todo_nudge_from_tools` — `return ""`
4. `forge/runtime.py:1344` `_sync_system_hint` — `return ""`
5. `forge/sync/sync_layer.py:144` `_path_from_memory_write` — 内层 `return None`（顺带顶层补 `import sys`）
6. `forge/sync/sync_layer.py:402` `_build_diff_hint` — `return ""`
7. `forge/adapters/mastodon.py:164` `maybe_toot_git_event` — `return None`
8. `forge/projections/file_projection.py:77` `_get_path` — 解码失败 fallback `self._resolve(val)`
9. `forge/projections/file_projection.py:89` `_get_content` — 解码失败 fallback `val`
10. `forge/projections/file_projection.py:102` `_get_operations` — `return None`
11. `forge/tools/session_changes.py:67` `pending_direct_disk` — `continue`
12. `forge/tools/session_changes.py:145` `load_into_memory` — `continue`

每处只加 `print(f"[模块] 操作名 failed: {e}", file=sys.stderr)`，保留原 return 默认值 / continue，不改控制流、不改返回语义。

### 对上轮报告的 3 处纠正
- `mastodon.py` 实际行号 164（报告写 163）。
- `session_changes.py:96` 是 `out.append(line)` + `continue`（无法解析的行原样保留，有注释），非静默默认值，跳过。
- `file_projection.py:77/89` 的 `except` 返回的是 fallback 值（`self._resolve(val)` / `val`）而非 `None`，但仍属「解码失败吞异常无日志」，一并加日志。

### 结果
- 静默默认值（`return ""/None/continue` 且无任何日志）：本批 12 → 0
- 剩余非静默 `continue` 模式：`local_tools.py:81/539/561` 的 `skipped.append(f"{rel_path}: {e}")`（错误已记入 skipped 列表回显）；`session_changes.py:97` 的 `out.append(line)`（有意保留）
- 全量测试：395 passed，10 skipped（无回归）

### 真实遗留问题（其它类型静默默认值，属 P3-2~P3-6，未动）
- `return False`（无日志）：`core/backup_manager.py:28`、`projections/git_projection.py:43`、`tools/direct_disk.py:52`、`sync/sync_layer.py:556`（`world_available`）
- `return {}`（无日志）：`tools/project_memory.py:20`
- 纯 `pass` 剩余 2 处（第一批遗留）：`checkpoint.py:51`、`sync/state.py:65`

## P3-1 第三批：静默 return False/{} + 剩余 pass 清理（2026-08-23）

P3-1 收尾。只清最后 7 处，不碰 P3-2~P3-6，不推远程，未 commit。

### 清理的 7 处（`except Exception` 无日志 → 加 stderr 日志）
`return False`（4 处）：
1. `forge/core/backup_manager.py:28` `BackupManager.restore` — 补顶层 `import sys`
2. `forge/projections/git_projection.py:43` `_is_git_repo` — 补 `import sys`
3. `forge/tools/direct_disk.py:52` `world_available` probe — 补 `import sys`
4. `forge/sync/sync_layer.py:556` `SyncLayer.world_available`（文件已有 sys）

`return {}`（1 处）：
5. `forge/tools/project_memory.py:20` `load_memory` — 补 `import sys`

纯 `pass`（2 处，`.broken` 重命名兜底，主 load 失败已打日志）：
6. `forge/projections/checkpoint.py:51`（`sys` 已在 `_load` 内局部 import，作用域覆盖）
7. `forge/sync/state.py:65`（同上）

每处只加 `print(f"[模块] 操作名 failed: {e}", file=sys.stderr)`，保留原
`return False` / `return {}` / `pass`，不改控制流、不改返回语义、不把 pass 改成 raise。

### 结果
- 全量测试：395 passed，10 skipped（无回归）

## P3-1 全部完成（三批共 29 处）

| 批次 | 类型 | 数量 |
|------|------|------|
| 第一批 | 纯 `pass`（完全静默） | 10 |
| 第二批 | `return ""` / `return None` / `continue`（静默默认值） | 12 |
| 第三批 | `return False` ×4 + `return {}` ×1 + 纯 `pass` ×2 | 7 |
| **合计** | | **29** |

P3-1 目标（清完裸 `except Exception` 中完全静默 / 无日志的默认值）达成。
三批全量测试均 395 passed / 10 skipped，无回归。

### 真实遗留问题（属 P3-2~P3-6，未动）
- `local_tools.py:81/539/561` 的 `skipped.append(f"{rel_path}: {e}")`：错误已记入
  skipped 列表回显，非静默。
- `session_changes.py:97` 的 `out.append(line)`：无法解析的行原样保留（有意），非静默。
- 全库其余 `except Exception` 中的非静默形态（有日志 / 返回默认值 / continue）按
  P3-2~P3-6 分批。

## P0-1/P0-2 投影失败检查（已修复）
- 根因：mutation 主路径在 `projections.project()` 后不检查 `success`，与
  `confirm_fn`（失败→ToolResult.fail）语义不一致；模型会把 disk=FAIL 当成功。
- 修复：`intent_tools.py` 新增 `_failed_projections` / `_projection_failure_result`；
  `_register_path`、`_write_content_to_world`、create_object/create_file/modify_file/
  edit_files_batch/delete_file/link_objects/unlink_objects/apply_patch 全部在投影失败时
  返回 `ToolResult.fail`（payload `projection_failed=True`），且失败时不写 path_map。
- 说明：World 事务已提交无法自动回滚；失败文案引导 `forge_sync` 对账。

## 已知技术债（未处理）
- 全库 158 处 `except Exception`（非裸 `except:`）；P3-1 第一批已清 10 处纯 `pass`，
  剩 2 处纯 `pass`（checkpoint.py:51 / sync/state.py:65，`.broken` 重命名，主失败已日志），
  其余有日志/返回默认值/continue，留待 P3-2~P3-6 分批
- ~~`forge: tx=NN v=NN` 自动commit与人类feature commit混线~~ 已解决：
  自动事务提交已停用（git_projection.apply() 不再 commit），历史中的
  `forge: tx=` 提交已通过 filter-branch 全部移除
- veritas_kernel 侧：object_birth 收窄 pub(crate)、WAL截断恢复测试，完全未碰
- ~~forge/intents/intent_tools.py 投影结果被静默丢弃~~ 已修复（见上 P0-1/P0-2）
- ~~forge/projections/base.py:122-133 死代码~~ 已在 acaf7c4 删除；本条仅文档过时

## 生产路径不变

---

## World ↔ Disk 同步架构重构 — STATUS

### 一、为什么要改

Forge 原本假设磁盘只有 World 一个写者：World 记录每次工具操作的 receipt，启动时把这些 receipt "重放"到磁盘，用来恢复状态。

但实际使用中，用户会绕开 Forge 直接手动 git commit 改代码。这产生了第二个写者，单一写者假设被打破：

- World 不知道用户手动改过什么
- 启动时 World 仍试图按旧 receipt 重放/覆盖磁盘
- 发现磁盘已经变了 → 跳过（skip），但仍把 checkpoint 当作"已同步"推进

结果：World 记录的状态和磁盘实际状态越分越远，每次启动刷警告，且两边永远对不齐。

核心问题：checkpoint 在没有真正同步成功的情况下被推进，即"检测到问题但假装没事"（内部称为"装瞎"）。这不是一处 bug，是架构假设本身错了。

### 二、新架构是什么样

原则：磁盘 + Git 是唯一的内容存储地；World 只记录历史事实，不主动写磁盘。

不是"磁盘说了算、World 说了算"这种谁当老大的问题——两边谁的进度更新，纯粹取决于用户刚才在用哪一头（手动 git 还是用 Forge 工具），没有默认赢家。

真正变化的是：World 不再有权力主动覆盖磁盘，只负责被动对账，检测到状态对不上就停下来问人，绝不自己选边、绝不假装同步成功。

新增一层 Sync Layer，做三态判定：

| 状态 | 含义 | 处理方式 |
|------|------|---------|
| IN_SYNC | World 记录的基线和磁盘/Git 一致 | 正常继续 |
| FAST_FORWARD | 只有一边前进，无冲突 | 对齐指针即可，一句话确认 |
| CONFLICT | 两边都变了，或出现无法判定的情况 | STOP，展示差异，等人决定 |

同时新增 WORLD_UNAVAILABLE：World（veritasd）连不上时，明确返回"不知道"，不能被静默当成"没有变化"去伪装成 IN_SYNC。

Checkpoint 拆成两套水位，避免"消费进度"和"磁盘真实同步进度"混为一谈：

- receipt_consumed_version：receipt 处理到哪了（纯记账，给只读投影用）
- disk_synced_version：磁盘真正确认同步到哪了（只有真正落盘成功才推进）

### 三、做了什么（按顺序）

1. Sync Layer 落地：新增 .forge/sync_state.json，记录 last_known_commit 和文件 hash 基线，作为三态判定的依据。要求工作区必须是 Git 仓库（非 Git 场景不支持，按现有工作流不会发生）。

2. 收窄 ProjectionRecovery：删除"用 receipt 重放写盘"的逻辑。Recovery 现在只做状态检测，检测到分叉就 STOP，不再有任何"跳过后仍推进 checkpoint"的路径。

3. FileProjection 区分场景：用户确认后的正常工具写盘（Intent→commit→project）保留不变；Recovery/同步路径禁止隐式覆盖磁盘。

4. Receipt 增加来源标记：区分 forge_tool（Forge 工具做的）和 external_sync（用户手动同步进来的），历史审计不再含糊。

5. veritasd 离线处理：detect() 和运行时写操作统一识别 WORLD_UNAVAILABLE 并 STOP。此前的实现会把"连不上 World"错误地当成"World 没有新变化"，等同于伪造了一个安全结果；修复后不可达就是不可达，不写盘、不同步。

6. 外部新建未跟踪文件检测（缺口 #1）：此前 Forge 只比对"已知文件"的 hash，如果磁盘上冒出一个 Forge 从未见过的新文件，会被漏判成 IN_SYNC。现在用 git status 里的未跟踪文件列表，减去 Forge 已知路径（已同步 hash 记录 ∪ receipt 历史里出现过的路径），剩下的判定为外部新建，触发 CONFLICT（标记为 untracked_external，与内容分叉类冲突在提示信息上区分开）。

7. 批量写入过程中的窗口保护（缺口 #4b）：此前"检测"只在每次工具调用前查一次，如果一次操作要写多个文件，写第一个和写第二个之间磁盘被外部改了，完全无法感知，会被无声覆盖。现在 FileProjection.apply 在写前对目标文件记录 hash 快照，每写一个文件前重新校验一次，发现漂移立即停止整批写入，并尝试用已有的 backup 机制回滚已写部分；如果回滚本身也失败，相关路径标记为"未知状态"并从已知集合里移除，不会被当作"没问题"混过去。

8. 单文件写入原子化：写文件的方式从直接覆盖改为"写临时文件 + 原子替换（os.replace）"，避免单个文件写入过程本身被外部同时写入而产生半成品文件。

### 四、明确不做的（Non-Goal）

- 不做三路自动合并：检测到冲突只展示差异，不自动 merge。设计如此，冲突必须由人决定，不是 bug。
- 全局同步水位，非逐文件：disk_synced_version 是单一数值，粒度较粗，但正确性没问题，属于可接受的精度取舍。
- GitProjection 保持空壳：不自动 commit，Git 相关的比对逻辑都在 Sync Layer 里，职责划分如此。
- LLM 思考期间的外部修改不监听：这段时间没有任何写操作在进行，风险可控，留到后续如需要再引入 inotify/文件锁等重型方案。
- detect() 每次全量扫描 receipt 历史：暂不做缓存，后续 receipt 量级增长后再优化，目前是已知技术债，不是遗漏。

### 五、结果

全量测试 189 passed，改动已合并推送。

一句话总结：以前 World 想当磁盘的主人，对不上就装瞎继续走；现在 World 只负责观察和记账，磁盘和 Git 是唯一的内容来源，任何时候只要情况不明确，一律停下来问人，不再有任何"跳过后假装同步成功"的路径。

## 多模型接入 + 会话异常止血（2026-08-21）

### 做了什么
- 新增 OpenAI 兼容适配器层（openai_compat.py），接入智谱 / OpenRouter
- 新增 zp.py / or.py 入口；移除 Groq（免费额度不稳定）
- dp.py / gg.py 支持环境变量换模型
- 四个入口主循环统一异常处理：traceback + 保存会话 + break
- openai_compat.py 处理 OpenRouter 网关空响应（choices=None），不再 NoneType 崩溃

### 实测免费模型问题
1. 智谱 glm-4.6v 赠送 token 实际路由到 Claude 3.5，模型 ID 与入口名不符
2. OpenRouter 免费模型被路由到 Claude 3.5，实际模型不可控
3. Gemini 免费档频繁 429，多轮工具循环不可用
4. 长任务中模型"失忆"，重复结论，甚至把文件路径答错
5. 工具循环中间输出过多，结论被淹没

### 未解决
- openai_compat.py 无 429 重试逻辑（gemini.py 已有）
- 模型名显示与实际不符，启动横幅不可信
- 长任务无结构化"计划-验证-结论"骨架
- 免费模型只适合单次问答和短任务，不适合多步工具审计

## tui_input 交互输入三连修复（2026-08-21/22）

### 背景
手机 Termux 场景下，从 App 复制多行内容粘贴进 `dp` 时，换行被当成"发送"，消息被截断；后续又出现"打一行字冒出十几行一模一样内容"的重绘错位。

### 做了什么
1. **2788cc2** — 多行粘贴不再截断  
   - 新增 `forge/tui_input.py`，启用 bracketed paste（`\x1b[?2004h`）。  
   - Termux 等支持终端把粘贴内容包在 `\x1b[200~` … `\x1b[201~` 里，内部换行按字面处理，整体等用户再按 Enter 才提交。  
   - 不支持 bracketed paste 的终端用行尾 `\` 手动续行兜底。  
   - `dp.py` 主循环改用 `read_multiline_input`，替换内置 `input()`。

2. **3d28c1e** — 中文输入变成问号  
   - 根因：raw 模式下逐字节 `os.read(1)` + 单独 decode，UTF-8 多字节字符（如「你」= E4 BD A0）被拆成替换符 U+FFFD。  
   - 修复：按首字节判断序列长度，读齐后再一次性 decode。

3. **b02dab3** — 折行重绘错位（打一行字冒出多行重复）  
   - 症状：每按一键就在新行重写整条内容，屏幕刷出十几行一模一样的字。  
   - 根因：旧 `_render` 靠「预测折行数」算相对光标上移量 `\x1b[{up}A`。宽字符/emoji 折行，或终端行尾「待折行」状态一旦与预测不一致，光标就错位。  
   - 修复：写提示符前用 `DECSC(\x1b7)` 记住提示符行首**绝对位置**，每次重绘 `DECRC(\x1b8)` 跳回 + `\x1b[J` 清到屏尾 + 整段重写。彻底不依赖折行预测。删除了 `_char_cols` / `_count_render_lines` / `prev_lines` / `width` 整套逻辑。  
   - ⚠️ **该方案已被推翻**：DECSC/DECRC 在 Termux 实测仍复现重复问题（2026-08-22 第四轮，见文件尾部），当前代码已改用字符宽度计算 + 相对上移 + 逐行清屏方案。  
   - 多行粘贴与 UTF-8 中文不受影响。测试 +95 行（含 MiniVT 模拟宽字符折行回归）。

### 结果
- 相关测试：`tests/test_tui_input.py`（含长中文折行不重复断言）。  
- 生产路径：仅影响 `dp` 交互输入层，不影响工具循环 / World / Projection。

### 已知残留风险（Termux）
- 若本地仍出现「打一行冒多行」，优先确认已拉取到 `b02dab3`。  
- DECSC/DECRC + `\x1b[J` 在部分 Termux 版本/软换行边界上可能仍有差异，若复现需再针对该终端补 CUP 绝对定位或备用重绘策略。

## tui_input 折行重复问题修复总结（第四轮，2026-08-22）

问题：Termux（华为 P20，64 列）输入中文时，每按一键刷出十几行重复内容。

根因：旧版用 `\x1b7` 保存光标、`\x1b8` 恢复。Termux 在软折行/屏幕滚动后，恢复位置不准，从错误位置重写导致累积重复。

修复：放弃 DECSC/DECRC，改用字符宽度计算 + 相对上移 + 逐行清屏。

- `_char_width()` 用 `unicodedata.east_asian_width` 精确判断中文/emoji 占 2 列（组合符 0 列）
- `_display_lines()` 计算文本占用的物理行数
- `_render()` 用 `\x1b[{n}A` 上移 + 逐行 `\x1b[2K` 清行后重写，返回新行数供下次用
- 状态机增加 `prev_lines` 追踪，每次精确清除上一轮内容

结果：17 个测试全绿，全量 176 通过（1 个无关旧失败）。实际运行不再出现重复行。

关键变化：从依赖不可靠的终端保存序列，改为基于精确计算的基础 ANSI 序列，兼容性更好。

## run_legacy / confirm_fn 废弃链确认（2026-08-22）

### 核实结论
- `run_legacy` 没有任何调用点（全仓库仅定义 + DEPRECATED 注释）
- `confirm_fn` / `abort_fn` 只被 `run_legacy` 调用，生产路径 `Runtime.run → _run_conversation` 不经过它们
- `forge/agent_state.py` 标注 "kept for run_legacy"，属废弃链
- ⚠️ 原结论误判：`forge/confirmation.py` 的 `is_confirm`/`is_cancel` 仍被 `_handle_plan_reply`（计划确认流程）使用，并非整文件废弃（仅 `extract_confirmation` 无人引用）

### 关联
- P0-2 修复时选择"对齐语义"而非删除旧路径，因为 `confirm_fn` 仍被 `run_legacy` 引用

### 状态
- ✅ 已处理（2026-08-23 runtime 结构层清理第 1 轮）：删除 `run_legacy` / `confirm_fn` / `abort_fn` / `agent_state.py`；`confirmation.py` 保留 `is_confirm`/`is_cancel`，删除 `extract_confirmation`

## P0-4 第 3 批测试质量记录（2026-08-22）

### 情况
- 第 3 批修复了 intent_tools / runtime 的副作用失败可观测性，5 个测试全过
- 但 5 个测试中只有 1 个（`test_save_session_summary_failure_is_logged`）直接验证了修复点
- 其余 4 个测试存在以下问题：
  - 只测 helper 函数本身，不测工具调用链
  - 手动构造 ToolResult + 手动 raise + 手动调 helper，等价于"自己拿锤子砸钉子"
  - `test_sync_path_map_failure_does_not_raise` 完全没有调用 `_sync_path_map`，只是手动 catch mock 异常

### 影响
- 测试全过不破坏功能，但提供虚假安全感
- 如果将来有人删掉 `str_replace`/`write_file` 里的 `_note_side_effect_failure` 调用，这些测试仍然全绿，发现不了回归

### 后续
- 做 P1-4（验证闭环做硬）时补上真正的端到端测试
- 测试应通过 `tools["str_replace"](...)` / `tools["write_file"](...)` 入口，注入副作用失败，断言 success=True + payload.side_effect_warnings + display 含 SIDE_EFFECT_WARN

## P0-4 全部五批完成（2026-08-22）

### 修复内容
- 第 1 批：FileProjection.apply 读原文件/备份失败 → 硬失败，不写盘不删除
- 第 2 批：forget_paths 失败 raise；ensure_identity 失败中止启动；path_map 重建失败标记 degraded
- 第 3 批：工具成功后副作用失败（record_tx/memory/cache/session_summary）→ stderr + payload.side_effect_warnings，success 保持
- 第 4 批：ProjectionResult 加 warning 字段；mark_disk_synced 失败可观测，success 保持
- 第 5 批：git_utils 真实故障抛 GitError；sync_layer prepare 失败不伪装"无冲突"；local_tools 单文件失败可观测；prepare 读失败不退化

### 测试
- 全量 pytest：237 passed，10 skipped（veritasd 二进制缺失，环境问题）

### commit
- batch1: afb8691
- batch2: 98bac4f
- batch3: 1a16e96
- batch4: ba4aab2
- batch5: b4dafa7

### 遗留
- 第 3 批测试质量差（4/5 安慰剂），记录在上方，P1-4 时补端到端
- 其余裸 except 非关键路径未清（P3-1）
- run_legacy/confirm_fn 废弃链未删（记录在上方）

## P1-1 + P1-2 完成（2026-08-23）

### P1-1 Working Set
- 新增 `WorkingSet` dataclass：goal / constraints / files_read / files_edited / open_hypotheses / pending_verify
- Runtime 内存持有，任务开始建 goal，工具调用后增量更新
- 每轮注入 `[Working Set]` system 摘要，≤28 行
- 读操作记 files_read，mutation 成功记 files_edited + pending_verify，失败/NEAR_MISS 记 open_hypotheses

### P1-2 压缩重写
- `_compress_messages(..., working_set)` 基于 Working Set：
  - 相关 path 优先保留（files_read / files_edited）
  - read_file/search_code/str_replace 的 NEAR_MISS 最近 2 次不压
  - 确认型压一行但保留 path + tx
  - 无关旧输出优先压缩
  - [Working Set] system 消息不压缩不丢弃

### 测试
- P1-1: 6 个契约测试，全过
- P1-2: 6 个回归测试，全过
- 全量：259 passed

### 测试质量问题（待统一修复）
1. `test_compress_retains_goal_via_working_set` 断言极弱（`assert len(out) >= 1`），基本只验证"没崩"，没有真正验证 goal 保留
2. `test_compress_keeps_recent_near_miss` 只断言 "NEAR_MISS" 字符串存在，没验证具体内容完整（候选片段是否保留）
3. `test_compress_unrelated_history_is_compressed` 只断言"有压缩发生"，没验证具体哪些该压缩
4. `test_compress_confirmation_keeps_path_and_tx` 断言 `tx=99` 可能因空格/格式变化误判

### 遗留
- Working Set 仅进程内内存，跨 Runtime 实例不恢复
- path 提取依赖 display/payload 格式，非标准工具可能漏记
- open_hypotheses / pending_verify 未与 todo 双向同步清账
- 未推远程（按本轮要求）

## P1-3 + P1-4 完成（2026-08-23）

### P1-3 str_replace 失败反馈强化
- near_miss.py 新增 diagnose_mismatch（indent/whitespace/quotes 差异识别）、suggest_old_string（唯一模糊匹配 + 行号 + 可复制片段）、find_occurrence_lines（多命中行号）
- str_replace 失败时展示：差异类型、SUGGESTED old_string、NEAR_MISS 候选
- 多命中未 replace_all：列出前 3 处行号

### P1-4 编辑后验证闭环
- _attach_diff 有 related tests 时 display 顶部加 VERIFY_REQUIRED
- WorkingSet 扩展 failure_context / verify_targets
- run_test_structured 失败写 failure_context，成功清空 pending_verify / verify_targets / failure_context
- summary 注入 VERIFY_REQUIRED + failure_context

### 测试
- P1-3: 5 个专项测试，全过
- P1-4: 6 个专项测试，全过
- 全量：270 passed

### 遗留问题（后期统一解决）
1. ~~VERIFY_REQUIRED 靠模型遵守 Working Set / display 提示，Runtime 未硬拦截后续无关 mutation~~ → 已由 P1-6 解决
2. suggest_old_string 对复杂多候选可能不唯一，此时只给 NEAR_MISS 列表（不确定时不给错误建议比给错建议更安全）→ 仍保留
3. ~~pending_verify 在测试成功时整表清空。多文件并行编辑时可能一次清掉多项未验证项~~ → 已由 P1-5 解决

## P1-5 + P1-6 完成（2026-08-23）

### P1-5 pending_verify 精确清账
- WorkingSet 新增 `verify_map`（path → set(verify_target)）与 `failure_target` 两个内部字段
- 编辑成功时同时记录 path→target 关联；测试成功只清与本次 target 相关的
  pending_verify / verify_targets / failure_context（broad target `tests/` / `.` 覆盖一切）
- 测试失败保留 pending_verify / verify_targets，记录 failure_context + failure_target
- 无关联测试的编辑（无 verify_target）只被全量通过清除；多文件编辑验证 A 不再误清 B
- 测试：`tests/test_p1_verify_precision.py` 8 个契约测试，全过

### P1-6 VERIFY_REQUIRED 最小 Runtime guard
- 新增 `Runtime._guard_pending_verify`：verify_targets 非空时，对文件内容 mutation
  （str_replace / write_file / create_file / modify_file / edit_files_batch / apply_patch / delete_file）
  硬拦截「编辑非 pending 文件」
- 放行：read/diagnostic/test 工具、undo_last_tx、forge_sync、编辑仍在验证的文件
- 验证通过（verify_targets 清空）后自动恢复；不做权限系统/状态机
- 测试：`tests/test_p1_verify_guard.py` 14 个契约测试，全过

### 结果
- 全量 pytest：282 passed，10 skipped（veritasd 缺失，环境问题；基线 270 = 本环境 260 passed + 10 veritasd）

### 遗留
- ~~`verify_map` / `failure_target` 仅进程内内存，跨 Runtime 实例不恢复~~ → 已由 **P0 Verify State Continuity** 解决（见文末）
- guard 拦截的 mutation 会作为一次失败尝试记入 open_hypotheses（可接受，非 bug）


## 编号调整说明（2026-08-23）

原清单 P1-5（子代理结论结构化）和 P1-6（工具面对齐）编号与本次"验证精确清账 + 硬拦截"冲突。

调整：
- 原 P1-5 子代理结论结构化 → 改为 **P1-7**
- 原 P1-6 工具面对齐 → 改为 **P1-8**
- 本次"验证精确清账 + VERIFY_REQUIRED 硬拦截"保留为 P1-5 + P1-6

剩余未做：
- P1-7：子代理结论结构化
- P1-8：工具面与实现面对齐

## P1 遗留补充（2026-08-23，P1-8 收尾时发现）

### 未记录问题（补记）
1. `post_toot` 有副作用（外部发帖）但分类在 READ_ONLY_TOOL_DECLARATIONS，规划阶段也暴露；`delete_toot` 却在 MUTATION。分类不一致，后续可修
2. `TOOL_DECLARATIONS = list(READ_ONLY_TOOL_DECLARATIONS)` 命名误导——实际只含只读面，且未被生产代码使用。死常量/易混淆，后续清理
3. `subagent.py` 顶部 `from typing import Any, Callable` 为历史遗留未使用导入，后续清理

## P2-1 完成：无 Veritas 时的一等直写路径（direct_disk）（2026-08-23）

### 问题
veritasd 不可用时体验断崖：`Runtime._guard_external_change` 对所有 mutation 硬 STOP，
连纯文本编辑都做不了。而 str_replace/write_file 的语义本来就不依赖 World 事务。

### 实现
- 新增 `forge/tools/direct_disk.py`：`world_available()`（探测口径与
  `SyncLayer.world_available` 一致，走 `get_version()`；对象不可探测时**默认 True**，
  保证既有 Veritas 路径与既有 fixture 行为不变）、`next_tx_id()`（合成
  `direct-<ns>-<seq>`，供 shadow 栈与 session_changes 追溯）、`write_text()`。
- `intent_tools._write_content_to_world`：World 事务前先判可用性，不可用 → `_direct_disk_write`
  只写磁盘，payload 带 `mode=direct_disk / direct_disk=True / world_recorded=False`。
- str_replace / write_file：display 的 RESULT 行带 `mode=`，session_changes summary 带 mode，
  成功后 `_attach_direct_disk_note` 置顶一行 `DIRECT_DISK: mode=direct_disk …`
  （因为 `_attach_diff` 会用 format_block 重建 display，标识必须重新置顶）。
- shadow undo / session_changes 语义完全复用：两者本来只依赖磁盘 + 本地栈，
  合成 tx_id 让 `undo_last_tx` 在直写下照常回滚。
- `SyncLayer` 拆出 `disk_change_detected()`（纯磁盘侧），`external_change_detected()`
  改为 `not world_available() or disk_change_detected()`——行为不变，只是可分开调用。
- `Runtime._guard_external_change`：World 不可达时，`DIRECT_DISK_TOOLS`
  (`str_replace`/`write_file`/`undo_last_tx`) 放行到直写，**但仍执行磁盘侧外部变更检查**；
  其余 mutation（create_object/link_objects/... 这些只存在于 World、无磁盘等价物）继续硬 STOP。

### 边界（不要扩大）
- direct_disk 只解决"无 Veritas 时的文件写入"。World object 操作不得伪装成 direct_disk。
- Veritas 可用时不触发任何新分支，事务路径逐字不变。
- VERIFY_REQUIRED guard 在工具循环里先于 external-change guard 执行，direct_disk 不绕过它。
- 未改 Planner/Intent/WRI/Veritas，未引入新架构概念。

### 测试
- `tests/test_p2_direct_disk.py` 27 个契约测试（覆盖 A~H 全部要求）
- 全量：**327 passed, 10 skipped**

### 已知边界 / 剩余 P2
- **P2-1a 启动期降级**：`Runtime.__init__` 里 `world.ensure_identity()` 失败仍然 raise
  （由 `tests/test_p0_batch2_consistency.py::test_ensure_identity_failure_aborts_runtime_init`
  锁定）。所以本轮的 direct_disk 覆盖的是"会话中途 veritasd 掉线"，
  不覆盖"veritasd 从一开始就起不来"。要做冷启动降级需要单独一轮并改那条既有契约测试。
- **P2-1b 其余文件 mutation 的直写**：`create_file` / `modify_file` / `apply_patch` /
  `edit_files_batch` / `delete_file` 目前在 World 不可达时仍硬 STOP。
  write_file 已能覆盖创建与整体覆写，故未纳入本轮最小范围。
- **P2-1c 复线对账**：direct_disk 写入不产生 receipt，恢复 veritasd 后需要 `forge_sync`
  把磁盘变更 FAST_FORWARD 回 World；目前只在 display 里提示，没有自动提醒/记账。
- 每次 str_replace/write_file 会多一次 `get_version()` 探测往返（与 guard 的探测重复）。
  量级很小，若成为热点再加进程内短 TTL 缓存。

### 顺带发现的既有缺陷（P2-1 未修，避免扩大范围）
- `write_file` 的「覆盖了已存在文件(N行)…建议用 str_replace」提示是**死代码**：
  它写进 `result.display` 后，紧接着 `_attach_diff` 用 `format_block` 整体重建了 display，
  提示被丢弃。**World 路径与 direct_disk 路径都一样**，即该 P1 提示从未真正到达模型。
  修法是把它并进 `_attach_diff` 的 body/hint，属独立一小轮，不在 P2-1 内。

## P2-2 完成：启动/冲突提示进入首轮 system 上下文（2026-08-23）

### 问题
`_startup_sync_check` 发现同步状态后只写 stderr，Agent 首轮看不到，仍会盲目
mutation 或不知道下一步该做什么。

### 实现
- `forge/runtime.py` 新增模块级纯函数 `_sync_status_system_hint(report)`：把
  `SyncReport` 格式化成结构化、可执行的首轮 system 提示（复用现有 `SyncReport`，
  不重做同步模型）。
  - CONFLICT：`sync=CONFLICT` + 禁止 mutation + 优先 `forge_sync` + 首轮回复解释冲突。
  - FAST_FORWARD（两方向）：`sync=...` + 明确方向（Disk → World / World → Disk，
    以 SyncReport 实际结果为准）+ 先 `forge_sync` + 同步完成前禁止 mutation。
  - IN_SYNC：`sync=IN_SYNC`，不阻塞。
  - WORLD_UNAVAILABLE：World 不可达；允许 direct_disk 文件工具、纯 World 操作不可用。
  - NOT_A_GIT_REPO：同步能力不可用。
- `Runtime._sync_system_hint()`：调用现有 `sync_status()` 取报告并格式化；探测失败退回空串。
- `Runtime._initial_system(extra_system)`：构建首轮 system（base 指令 + sync 状态 +
  阶段指令 + 摘要 + 记忆），sync 状态只在首轮构建时注入一次。
- `_run_conversation` 改用 `_initial_system`，注入真正进入 Agent 首轮可见的 system 消息
  （不是 stderr / assistant 文本），工具循环后续轮次只追加 Working Set / todo 提醒。

### 边界（不要扩大）
- 纯文本追加，不改 `_guard_external_change` / 工具声明 / direct_disk / sync 状态机。
- 复用 `sync_status()` / `SyncReport`，未重复实现同步或权限逻辑。

### 测试
- `tests/test_p2_2_sync_system_hint.py` 10 个契约测试（覆盖 CONFLICT / 两向
  FAST_FORWARD / IN_SYNC / WORLD_UNAVAILABLE / NOT_A_GIT_REPO / 只注入一次 /
  进入真实 system 消息 / 不改 mutation 路径）。
- 全量：**337 passed, 10 skipped**

## P2-3 完成：弱模型 / 免费模型长任务骨架（2026-08-23）

### 问题
弱模型在长工具循环里会丢失「现在要完成什么 / 已经完成什么 / 下一步 / 有什么风险」，
表现为重复搜索、失忆、无意义工具调用，并在接近步数上限时仍在盲目探索。

### 实现
全部复用现有 WorkingSet，**没有**新增 ProgressState / TaskState 之类平行状态系统。

- `forge/runtime.py` 新增常量 `PROGRESS_CHECKPOINT_EVERY = 5`、
  `FINAL_CHECKPOINT_TAIL_STEPS = 3`。
- 新增模块级纯函数（全部只读 WorkingSet，字段为空写「无」，不编造）：
  - `_progress_checkpoint_text(ws)` → `[PROGRESS]` + `goal/done/next/risk` 四行 +
    「在继续任何工具调用之前先输出上面 4 行」的强制 checkpoint 措辞。
    goal ← `ws.goal`；done ← `files_edited` 末 3 项；next ← `verify_targets`
    （优先，给出 `run_test_structured(target=...)`）否则 `pending_verify`；
    risk ← `open_hypotheses` 否则 `failure_context`。
  - `_final_checkpoint_text(ws)` → `[FINAL CHECKPOINT]` + `goal/done/unfinished/next`
    + 停止无关探索 / 已完成则不要继续调用工具。
  - `_checkpoint_for_step(ws, step_i, mutation_pending, max_steps)` 决定本轮注入哪种。
  - `_is_checkpoint_message(m)` 识别本机制的瞬时注入。
- `_run_conversation` 最小接入（3 处）：
  1. 每轮开头把上一轮的 checkpoint 消息丢弃（**瞬时**注入，每轮重建，
     与既有 `[Working Set]` 注入同一生命周期，不进 `self.conversation` 持久历史）。
  2. `adapter.send` 前按 `_checkpoint_for_step` 追加一条 system 消息。
  3. 工具结果 → 原有成功处理 → `working_set.update_from_tool` 之后，
     用**既有** mutation 成功判定（`tc.name in MUTATION_TOOL_NAMES and result.success`）
     置 `mutation_pending`，由下一轮注入 checkpoint。

触发规则：
- 周期：`step_i > 0 and step_i % 5 == 0`。
- mutation 成功：下一轮补一次（失败的 mutation 不触发，也不进 done——done 只来自
  `files_edited`，而它本就只在成功时入账）。
- 收束：`step_i >= MAX_AGENT_STEPS - 3` 改注入 `[FINAL CHECKPOINT]`，取代周期 progress。

### 边界（不要扩大）
- 纯上下文提示：不新增工具、不改 schemas、不改 mutation / transaction / projection 行为，
  不改 `MAX_AGENT_STEPS` 与循环终止逻辑，不阻止任何工具调用。
- 未加重复调用检测 / 相似度检测 / 输出分类器 / 新 Planner / 新 memory / 新 scheduler。
- 未动 Veritas、direct_disk、sync_layer，未改 `_sync_status_system_hint()` 语义；
  P2-2 首轮 sync system hint 仍是独立那条 system 消息，checkpoint 不覆盖不重写
  （含 CONFLICT 时「禁止 mutation / 必须先 forge_sync」约束）。

### 测试
- `tests/test_p2_3_progress_skeleton.py` **18 个契约测试**（每 5 步 checkpoint /
  周期反复出现 / 瞬时不累积 / mutation 成功触发 / 失败不触发且不进 done /
  Working Set 四字段进入 checkpoint / next 优先 verify_target / done 上限 3 项 /
  空 Working Set 用「无」/ checkpoint 长度受控 / FINAL CHECKPOINT 形状与最后 3 步 /
  短任务不受影响 / P2-2 sync hint 不回归 / Working Set 注入仍在 /
  不新增工具 / 不进持久 conversation）。
- 全量：**355 passed, 10 skipped**

### 真实遗留问题
1. `forge_sync` / `undo_last_tx` 也在 `MUTATION_TOOL_NAMES` 里，成功后同样会触发一次
   checkpoint。这是复用既有 mutation 判定的直接后果，语义上可接受（都改变了状态），
   但如果后续想让 checkpoint 只跟「文件编辑」，需要改判定集合而不是加字符串猜测。
2. 同一轮内多个成功 mutation 只合并成下一轮**一次** checkpoint（有意为之，避免重复注入）。
3. checkpoint 只保证信息进入下一轮上下文，**不校验**模型是否真的复述了那 4 行——
   本轮明确不做输出分类器。
4. `pending_verify` / `verify_targets` 都为空时 `next: 无`，此时 checkpoint 对下一步
   没有实际指导；这是「不编造信息」的直接代价。

## P2-4 完成：TUI 输入（Termux）回归测试锁死（2026-08-23）

### 目标
把 `forge/tui_input.py` 已修好的折行 / 重绘行为用回归测试锁死，防止后续修改再次破坏。
只补测试，不改实现算法。

### 审查发现的测试质量问题（情况 A，非实现 bug）
- **harness 缺口**：`read_multiline_input` 不暴露 `width`，`_render` 一直用
  `_get_terminal_width()`（真实终端，测试环境 = 80），而 MiniVT 模拟的是 10 / 64。
  因此现有 MiniVT 测试从未在「窄屏宽度」下真正驱动 `_render` 的折行路径。
- **弱断言**：现有 `test_long_wrapping_line_does_not_duplicate` /
  `test_narrow_termux_width_no_duplicate` 用 `count(text) == 1` 判断「不重复」，
  但实测其屏幕是 `['> 你好世界', '> 你好世界', '> 你好世界', '，测试']`
  ——**已经重复 3 次**，折叠后 `count` 恰好只匹配一次而侥幸通过，属于安慰剂测试。
- 结论：`tui_input.py` 生产路径（`_get_terminal_width()` 用真实宽度 + 相对上移 +
  逐行 `\x1b[2K` 清屏）逻辑正确，本次不修实现。

### 新增回归测试（tests/test_tui_input.py，11 个）
统一走 `_read_loop` 并注入 `width=vt.width`（`_run_input` helper），让 `_render` 的
折行宽度与 MiniVT 一致；断言改用**精确等值**（`"".join(screen()) == prompt + buffer`）
而非易被折叠逃逸的 `count`：

- B emoji：`test_display_lines_emoji` / `test_emoji_wrap_no_duplicate_no_truncation` /
  `test_emoji_cursor_position`
- C 多行粘贴渲染不重复：`test_paste_multiline_no_render_duplicate`
- D 退格重绘：`test_backspace_ascii_rewrites_exactly` / `test_backspace_deletes_wide_char` /
  `test_backspace_cross_wrap_clears_old_line`
- A/E 中文/长文本折行 + 光标：`test_chinese_wrap_cursor_position` /
  `test_long_text_cursor_position_at_end` / `test_long_text_backspace_no_residue`
- F 窄终端（64 列）退格：`test_narrow_backspace_no_residue`

新增 `_cursor_pos` 作为独立 oracle，按同一套列宽/折行模型算出内容末尾的 (行, 列)，
校验 `_render` 之后光标落在内容末尾而非明显偏移。

### 结果
- `tests/test_tui_input.py`：28 passed。
- 全量：**366 passed, 10 skipped**（P2-3 基线 355 → 新增 11 全绿）。

### 遗留（本轮未做，避免扩大范围）
- 现有两个弱断言测试（`test_long_wrapping_line_does_not_duplicate`、
  `test_narrow_termux_width_no_duplicate`）仍用 `count` + 不注入宽度，建议后续
  一并改为 `_run_input` + 精确等值断言，才能真正锁死「不重复」。
- `read_multiline_input` 未暴露 `width` 参数（未改实现，符合本轮「只补测试」边界）；
  测试通过直接调用 `_read_loop` 注入宽度。若后续想让公开 API 可直接测窄屏，可
  加一个 `width=None` 透传参数（属最小 API 补充，非算法变更）。
- 未推远程（按本轮要求）。

## 测试质量统一修复 Batch 1（2026-08-23）

纯测试修复，不碰生产代码。处理 3 项记录在案的测试质量问题（安慰剂 / weak 断言）。

### 处理的 3 项

1. **测试质量-1（Batch3 4/5 安慰剂，`tests/test_p1_batch3_side_effects.py`）**
   - 原问题：4 个测试只手动构造 `ToolResult` + 手动调 `_note_side_effect_failure`，
     `test_sync_path_map_failure_does_not_raise` 甚至根本没调 `_sync_path_map`（手动
     catch mock 异常数 `len(errors)==2`）。删掉生产里 `_note_side_effect_failure` /
     path_map 告警调用点，这些测试仍全绿。
   - 强契约：走 `tools["write_file"]` / `tools["str_replace"]` 入口（direct_disk
     路径，复用 `test_p2_direct_disk.py` 的 offline world 模式），patch
     `record_tx` / `record_session_change` 抛异常，断言 `success is True` +
     `payload.side_effect_warnings` 含对应标签 + `display` 含 `SIDE_EFFECT_WARN:` +
     磁盘主操作本身确实完成。`test_sync_path_map_failure_does_not_raise` 改为
     World 路径下两条 path_map 更新路径都抛异常 → 工具仍成功 + 告警可观测。
   - 已验证：把 `_note_side_effect_failure` 替换成 no-op 后，新断言立即失败。

2. **测试质量-2（P1-2 weak 断言，`tests/test_p1_compress.py`）**
   - 原问题：`_compress_messages` 有 `len(messages) < 24` 的硬门；4 个 weak 测试里
     3 个消息数 < 24，根本没触发压缩（`assert len(out) >= 1`、`"NEAR_MISS" in joined`、
     `"tx=99" in joined` 都是天然通过）。
   - 强契约：4 个测试全部改到 ≥24 消息、> keep_recent_tools，真正触发压缩后断言
     精确契约 —— goal 经 `[Working Set]` system 消息逐字保留（且确有其它消息被压）；
     两条 NEAR_MISS 落到 recent 尾巴之外仍逐字保留（非被摘要）；无关历史精确
     26 压 / 4 留（recent 逐字保留、其余 `[compressed` 前缀）；confirmation 型
     write_file 摘要保留 path + tx。
   - 已验证：把 `_is_near_miss_or_fail_content` 改成恒 False 后，NEAR_MISS 逐字
     保留断言立即失败。

3. **测试质量-3（TUI 两弱断言，`tests/test_tui_input.py`）**
   - 原问题：`test_long_wrapping_line_does_not_duplicate` /
     `test_narrow_termux_width_no_duplicate` 用 `read_multiline_input(...)` 且不注入
     width，`_render` 一直用真实终端宽度 80，MiniVT 的 10/64 从未驱动折行；`count(text)==1`
     在屏幕重复 3 次折叠后仍侥幸通过。
   - 强契约：改用 `_run_input`（注入 `width=vt.width`）+ 精确等值
     `"".join(vt.screen()) == prompt + buffer` + 光标位置断言（证明确实走了窄屏折行）。

### 是否修改生产代码

全部为「否」——`forge/` 下所有实现文件未动，仅改 `tests/` 下 3 个文件。

### 测试数量

- 修改（非新增/删除）：3 个文件共 39 个测试，函数数量与 HEAD 完全一致（5 + 6 + 28）。
- 无新增、无删除测试函数；只把安慰剂/weak 断言重写为强契约。

### 专项与全量结果

- 专项：`test_p1_batch3_side_effects.py`（5 passed）、`test_p1_compress.py`（6 passed）、
  `test_tui_input.py`（28 passed）。
- 全量：**366 passed，10 skipped**（与 HEAD 基线一致；本轮不新增测试数）。

### 是否发现新的真实 bug

未发现。生产实现正确，本轮只补测试锁死既有行为。（注：任务给出的基线 376 与本仓
实际 HEAD 基线不符；实测 HEAD 全量为 366 passed / 10 skipped，与 P2-4 记录一致。）

### 遗留（不在本轮范围）

- `test_compress_does_not_destroy_working_set_message`（22 条消息 < 24）与
  `test_compress_keeps_edited_file_paths_visible`（25 条，仅 substring 断言）仍偏弱，
  但不在此次列出的 4 项 weak 清单内，未动。
- 未 commit、未 push（按本轮要求）。

## 测试质量统一修复 Batch 2（2026-08-23）

纯测试修复 + 一处公开 API 补充（`read_multiline_input` 加 `width` 透传，不改算法）。
处理 Batch 1 遗留的 2 项 weak 断言 + 1 项 API 补充。

### 处理的 3 项

1. **compress 测试-1（`test_compress_does_not_destroy_working_set_message`）**
   - 原问题：22 条消息（2 system + 20 tool）< 24，`_compress_messages` 提前
     `return messages`，压缩根本没触发。测试名说「压缩不摧毁 WS 消息」，实际
     只是验证「未压缩列表里 WS 消息还在」，恒真。
   - 强契约：消息数改到 30（≥24）确保真正触发压缩；新增断言「确有工具消息被
     摘要」（`[compressed` 前缀）；`[Working Set]` system 消息逐字保留（数量 ==1、
     内容 == ws.summary()、goal 子串在）。反例：压缩错删 WS 消息 → `len==1`
     或逐字等值立即失败。

2. **compress 测试-2（`test_compress_keeps_edited_file_paths_visible`）**
   - 原问题：25 条消息 ≥24，压缩确实触发，但断言是 substring（`"forge/runtime.py"
     in joined`、`"tx=77" in joined`）。即使 str_replace 被摘要成 `[compressed
     str_replace]\nRESULT: path=... tx=77 ...`（内容型摘要保留前 8 行），断言照样过。
   - 强契约：抽出 `edit_result` 精确原文，断言「确有无关消息被压缩」+「str_replace
     结果逐字保留」（`content == edit_result`，数量 ==1）。反例：相关 path 保护失效
     → str_replace 被压缩 → 逐字等值失败（已用 monkeypatch `_path_mentioned=False`
     验证：此时 content 前缀为 `[compressed str_replace]\n...`，等值必 False）。

3. **`read_multiline_input` 加 `width` 透传（`forge/tui_input.py`）**
   - 原问题：公开 API 不暴露 `width`，窄屏测试只能绕过它直接调 `_read_loop`。
   - 改动：签名 `read_multiline_input(prompt="> ", key_source=None, write=None,
     width=None)`，`width=None` 时行为完全不变（`_read_loop` 内部走
     `_get_terminal_width()`）；`width` 有值时透传给 `_read_loop`（测试注入路径 +
     tty 路径都透传）。纯 API 补充，不改算法。

### 是否修改生产代码

- `forge/tui_input.py`：**是，但仅为 API 补充** —— 给 `read_multiline_input` 增加
  `width=None` 关键字参数并透传，不改变任何既有逻辑/算法；`width=None`（现有调用方
  的默认）行为与改动前逐字节一致。
- `forge/runtime.py` 等其它实现文件：未动。

### 测试数量

- 修改（非新增）：`test_p1_compress.py` 2 个测试重写为强契约。
- 新增 1 个：`test_tui_input.py` 新增 `test_read_multiline_input_width_passthrough`
  （直接调公开 API `width=10` 验证窄屏折行 + 光标位置）。
- `_run_input` helper 简化：从直接调 `_read_loop` 改为走 `read_multiline_input(width=...)`，
  断言保持不变（精确等值）；`_read_loop` 从测试 import 移除。

### 专项与全量结果

- 专项：`tests/test_p1_compress.py` + `tests/test_tui_input.py` = 35 passed。
- 全量：**367 passed，10 skipped**（基线 366 + 新增 1 个 width 透传测试）。

### 是否发现新的真实 bug

未发现。生产实现正确，本轮只补测试锁死既有行为 + 纯 API 透传。

### 遗留（不在本轮范围）

- write_file 覆盖提示死代码（下一轮）。
- P2-1a/1b/1c、P3-1 ~ P3-6：未动。
- 未 commit、未 push（按本轮要求）。

## write_file 覆盖提示死代码 + P1 遗留 3 项小修（2026-08-23）

本轮处理 4 项，均为「P2-1 顺带发现」与「P1 遗留补充」记录在案的独立小修。

### 1. write_file 覆盖提示死代码（生产代码小修）

- **原问题**：`write_file` 把「覆盖了已存在文件(N行)…建议用 str_replace」提示写进
  `result.display`，紧接着 `_attach_diff` 用 `format_block` 整体重建 display，提示被
  丢弃，从未真正到达模型。World 路径与 direct_disk 路径都一样。
- **根因**：提示生成点（`write_file` 内拼进 RESULT 行）与 display 重建点
  （`_attach_diff`）错位；且触发条件用 `mode == "overwrite"`，direct_disk 路径下
  `mode == MODE_DIRECT_DISK`，提示根本不生成。
- **修复方式**：把覆盖提示并进 `_attach_diff` 的 `hint`，通过新增可选参数
  `overwrite_note` 传入；触发条件从「`mode == "overwrite"`」改为「`old_content.strip()
  and old_content != new_content`」，使 World 与 direct_disk 两路径语义一致。
  `_attach_diff` 内 `hint = overwrite_note + " " + hint`，display 重建后提示仍在。
- **未做**：未重构 `write_file` 其他逻辑；`mode` 仍用于 RESULT 行/payload 展示。
- **测试**：新增 `tests/test_write_file_overwrite_hint.py` 4 用例（World 覆盖含提示 /
  direct_disk 覆盖含提示 / 新建文件无提示 / 同内容无提示）。

### 2. post_toot 分类调整

- **原问题**：`post_toot` 有外部发帖副作用（Mastodon POST），却归类在
  `READ_ONLY_TOOL_DECLARATIONS`；`delete_toot` 同类外部删除副作用却在
  `MUTATION_TOOL_DECLARATIONS`，分类不一致。
- **修复方式**：把 `post_toot` 声明从 READ_ONLY 移到 MUTATION，置于 `delete_toot`
  之前，二者一致。schema 与 `make_tools` 注册（local_tools.py 的 `make_local_tools`
  返回字典本已含二者）对齐，无注册缺口。
- **测试**：新增 `test_mastodon_side_effects_are_mutations_not_read_only`
  （tests/test_tool_surface_alignment.py），断言 post_toot / delete_toot 均在 MUTATION
  且均不在 READ_ONLY。

### 3. TOOL_DECLARATIONS 处理方式

- **原问题**：`TOOL_DECLARATIONS = list(READ_ONLY_TOOL_DECLARATIONS)` 命名误导
  （实际只含只读面），且未被生产代码使用——全仓唯一引用是 `runtime.py` 顶部 import，
  该 import 后也无任何使用点。死常量/易混淆。
- **处理方式**：直接删除（定义 + `runtime.py` 未使用 import）。无测试引用，无需改测试。

### 4. subagent.py import 清理

- **删了什么**：`from typing import Any, Callable`。grep 全文确认 `Any` / `Callable`
  均未被使用（文件只用 `dict[str, list[str]]`、`str | None` 等内置泛型）。

### 测试数量变化

- 新增 5 个：write_file 覆盖提示 4 + post_toot 分类 1。
- 全量：**372 passed，10 skipped**（基线 367 + 新增 5）。

### 真实遗留问题

1. `post_toot` / `delete_toot` 进入 `MUTATION_TOOL_NAMES` 后，会命中 `_guard_external_change`
   的 mutation 分支（此前 post_toot 在 READ_ONLY 不命中）。该 guard 对「World 不可达」的
   mutation 会返回硬失败，但对 Mastodon 外部操作而言「World 不可达」本应无关紧要——
   Mastodon 走的是环境变量 + HTTP，不依赖 World。当前 `delete_toot` 已是同样处境，属
   既有行为而非本轮新引入，但语义上仍是「外部副作用工具被套了 World 可达性守卫」，
   后续若要彻底修需为 Mastodon 工具单独豁免（非本轮范围）。
2. `_EDIT_TOOLS` / `_VERIFY_GUARDED_MUTATIONS` 均不含 post_toot / delete_toot，故
   VERIFY_REQUIRED 硬拦截与 Working Set 的 files_edited 记账不受本轮影响，保持正确。
3. 未 commit、未 push（按本轮要求）。

## P2-1 补全：冷启动降级 + 其余 mutation 直写 + 复线对账（2026-08-23）

上一轮 P2-1 只覆盖了 str_replace / write_file / undo_last_tx 的直写；本轮补完
1a / 1b / 1c 三个遗留项。基线实测 **372 passed / 10 skipped**（任务给出的 382 与
本仓实际 HEAD 基线不符，同「测试质量 Batch 1」记录的 376 vs 366 现象；以实际为准）。

### P2-1a 冷启动降级

- **原问题**：`Runtime.__init__` 里 `world.ensure_identity()` 失败仍 `raise`，veritasd
  从一开始就起不来时 Runtime 无法启动，direct_disk 只覆盖「会话中途掉线」。
- **是否改了既有测试**：是。`tests/test_p0_batch2_consistency.py::
  test_ensure_identity_failure_aborts_runtime_init` 原语义（必须 raise）已过时——冷启动
  降级才是正确行为。改为 `test_ensure_identity_failure_degrades_runtime_not_aborts`：
  断言不 raise 且 `rt.world_available is False`。
- **修复方式**：`Runtime.__init__` 中 `ensure_identity` 失败不再 raise，置
  `self.world_available = False` 并 stderr 提示降级；`_startup_sync_check` 补
  `WORLD_UNAVAILABLE` 分支（此前该分支缺失，会把 WORLD_UNAVAILABLE 误报成
  FAST_FORWARD，降级落地后此路径才真正被走到）。
- **测试变化**：改 1 个测试的语义（raise → 降级），测试数量不变。

### P2-1b 其余文件 mutation 的直写

`DIRECT_DISK_TOOLS` 从 3 项扩到 8 项（新增 create_file / modify_file / apply_patch /
edit_files_batch / delete_file；create_object/link_objects/unlink_objects 无磁盘等价物，
仍不在此列）。每个工具的 direct_disk 等价语义：

- `create_file` = 本地写新文件（`write_text`）。
- `modify_file` = 把 machine ops 应用到本地文件（新增 `direct_disk.apply_edit_ops`，
  复用 FileProjection 同源的 `PatchEngine.apply_edits`，落盘结果与 World 路径逐字一致）。
- `edit_files_batch` = 逐文件应用 machine ops，共享一个 shadow undo 条目（undo 一次恢复全部）。
- `apply_patch` = 把 unified diff 直接落到本地文件（复用 `apply_unified_patch_to_files`
  已算出的 `old_content/new_content`，无需 machine ops）。
- `delete_file` = 本地删文件（需 `path`；仅 object_id 无法离线解析 → 硬失败）。

共享 `_finalize_direct_disk` 收尾：shadow undo 记账（写前内容，undo_last_tx 可回滚，
含 delete 恢复）+ session_changes（direct_disk=True 标记）+ cache invalidate +
`_attach_direct_disk_note` 置顶 mode=direct_disk。display 均带 `mode=direct_disk`，
payload 带 `direct_disk=True / world_recorded=False`。World 可达时行为逐字不变。

### P2-1c 复线对账

- `session_changes.record` 新增 `direct_disk: bool = False` 结构化字段；新增
  `pending_direct_disk(project_root)` 读持久化 `.forge/session_changes.jsonl` 过滤
  direct_disk 条目（跨进程/重启可用）。
- `runtime._direct_disk_reconcile_hint(project_root, world_available)`：World 已恢复且
  存在待对账文件时返回提示文本（列出文件 + 提示 forge_sync）；World 不可达或无待对账
  返回空串。`_initial_system` 首轮 system 注入该提示。
- `forge_sync`（tools/__init__.py）结果追加「direct_disk 待对账文件」清单。
- **只提示，不自动 fast-forward**：保持「不自动 merge」原则，对账方向仍由显式
  forge_sync 的 SyncReport 决定。

### 测试结果

- `tests/test_p2_direct_disk.py`：27 → **40 passed**（+13：P2-1b 七项 + P2-1c 六项）。
- 全量：**385 passed，10 skipped**（基线 372 + 13 新增，无回归）。

### 遗留（本轮不做，避免扩大范围）

1. `pending_direct_disk` 的标记**永不清除**：direct_disk 的 session_changes 条目会永久
   保留，导致每次启动/forge_sync 都重复提示已对账过的文件。本轮未引入「已对账」清账
   状态（P2-1c 只要求「记录 + 提示」，不新增状态系统），后续可在 forge_sync 成功把磁盘
   FAST_FORWARD 回 World 后清掉对应 direct_disk 标记。
2. `delete_file` direct_disk 用 path 定位；`apply_patch` 新文件（`--- /dev/null`）的
   shadow undo 前内容是空串，undo 会写成空文件而非删除该文件——与 write_file 新建文件
   的既有 undo 行为一致，可接受。
3. direct_disk 探测仍每次 `get_version()` 往返（与 guard 探测重复），量级小，未加缓存。
4. 未 commit、未 push（按本轮要求）。

## 剩余小修第一轮：Mastodon 豁免 / direct_disk 清账 / checkpoint 收窄（2026-08-23）

处理 3 项独立小修，均为前几轮 STATUS 里记录在案的真实遗留问题。基线实测
**385 passed / 10 skipped**（同 P2-1 补全记录）。

### 1. Mastodon 工具豁免 World 可达性 guard

- **原问题**：`post_toot` / `delete_toot` 进入 `MUTATION_TOOL_NAMES` 后，会命中
  `_guard_external_change` 的 mutation 分支。World 不可达时该 guard 对非
  direct_disk 的 mutation 返回硬失败，但 Mastodon 走环境变量 + HTTP，不依赖 World，
  也不写磁盘，「World 不可达」对它们无关紧要。（见「write_file 覆盖提示 + P1 遗留」
  一节真实遗留问题 1。）
- **修复方式**：`forge/runtime.py` 新增模块级 `_MASTODON_TOOLS = {"post_toot",
  "delete_toot"}`；`_guard_external_change` 在 forge_sync 豁免之后加一条 Mastodon
  豁免（早于 `sync_layer` 相关检查）。既豁免 World 可达性，也豁免磁盘变化——
  因为这两个工具根本不碰 World 或磁盘。
- **测试变化**：`tests/test_p2_direct_disk.py` 新增 2 用例
  （World 离线时豁免 / 磁盘变化时也豁免）。

### 2. pending_direct_disk 标记清账

- **原问题**：direct_disk 的 `session_changes` 条目永久保留，每次启动 / forge_sync
  都重复提示已对账过的文件。（见 P2-1 补全一节遗留 1。）
- **修复方式**：`forge/tools/session_changes.py` 新增 `clear_pending_direct_disk(
  project_root)`——重写 `.forge/session_changes.jsonl`，只移除 `direct_disk` 标记，
  保留条目其它字段（path/tx/tool/summary），不动其它 session_changes；无法解析的行
  原样保留。`forge/tools/__init__.py` 的 `forge_sync` 在 sync 前预检
  `detect().status == FAST_FORWARD_DISK_TO_WORLD`，sync 后 `status == IN_SYNC` 时
  调用清账（只清标记，不加新状态类型）。未完成对账（CONFLICT 等）不清。
- **测试变化**：`tests/test_p2_direct_disk.py` 新增 3 用例（只清 direct_disk 标记 /
  forge_sync 成功后清账且 display 不再列待对账 / CONFLICT 时不清）。

### 3. checkpoint 判定集合收窄

- **原问题**：`forge_sync` / `undo_last_tx` 也在 `MUTATION_TOOL_NAMES` 里，成功后
  会触发一次 `[PROGRESS]` checkpoint，但它们不是文件编辑，触发 checkpoint 无意义。
  （见 P2-3 一节真实遗留问题 1。）
- **修复方式**：`_run_conversation` 里 `mutation_pending` 的触发判定从
  `tc.name in MUTATION_TOOL_NAMES` 改为 `tc.name in _EDIT_TOOLS`（复用既有的文件编辑
  工具集合）。`forge_sync` / `undo_last_tx` 不再触发 checkpoint。
- **测试变化**：`tests/test_p2_3_progress_skeleton.py` 新增 2 用例（forge_sync 成功
  不触发 / undo_last_tx 成功不触发）。

### 验证

- 专项：`test_p2_direct_disk.py` + `test_p2_3_progress_skeleton.py` = 65 passed。
- 全量：**392 passed，10 skipped**（基线 385 + 新增 7，无回归）。

### 真实遗留问题

1. ~~`_EDIT_TOOLS` 仍含 `create_object`（纯 World 对象操作，非文件编辑），因此
   `create_object` 成功后仍会触发一次 checkpoint；但它无 path、不进 `files_edited`，
   checkpoint 的 `done` 为空，语义上只是「无意义的空 checkpoint」，非本轮范围。~~
   ✅ 已修复（2026-08-23 runtime 结构层清理第 1 轮）：`create_object` 已从 `_EDIT_TOOLS` 移除。
2. `clear_pending_direct_disk` 只清持久化文件，不清进程内 `_LOG` 的 direct_disk 字段；
   `pending_direct_disk` 读的是文件，故对提示无影响，但 `_LOG` 与文件在该字段上会有
   短暂不一致（仅在 long-lived 进程内可观察，无功能后果）。
3. `forge_sync` 清账前多一次 `detect()` 预检（World 往返），量级极小（forge_sync 属
   低频入口），未加缓存。
4. 未 commit、未 push（按本轮要求）。

## 遗留债：except Exception 改具体异常类型（暂缓）

### 状态
P3-1 的"消除完全静默"目标已完成（29 处）。但原始清单还要求"改成具体异常 + 日志"，目前只做了"加日志"，大部分 `except Exception` 仍是宽泛捕获。

**更新（2026-08-24）**：`_rebuild_path_map` 里 `get_receipts_since(0)` 抛异常静默 return 的「红区」已修（标记 degraded + 打 stderr，并接入 DEGRADED guard）；但「改成具体异常类型」的全量清理仍暂缓。

### 剩余工作
- 约 100+ 处宽泛 `except Exception` 可改为具体异常类型（如 `except OSError`、`except json.JSONDecodeError`）
- 需要逐处读上下文判断该用哪个具体异常
- 预计 3~5 轮才能完成

### 收益评估
- 生产行为不变，仅提高代码可读性和维护性
- 收益低于 P3-2（拆 local_tools）和 P3-6（429 重试）

### 决定
暂缓，等 P3 其他项完成后再单独处理。

## 遗留观察：模型绕过注册工具直接跑 Python（2026-08-23）

### 现象
发嘟文任务中，模型没有直接调用已注册的 `post_toot` 工具，而是：
1. 用 6 步搜索找到 post_toot 实现位置（search_code / glob_files / read_function）
2. 最后用 `run_command` 直接调 MastodonClient 发帖，绕过工具层

### 根因分析
1. `post_toot` 的 schema 描述太模糊（"发一条 Mastodon 嘟文。非强制：想发就调"），模型不确定参数格式和用途
2. 模型倾向"直接写代码更可控"，而非信任注册工具
3. 工具发现效率低：post_toot 在 meta_tools.py，但模型先搜 mastodon.py 导致 read_function 失败

### 改进方向
- ✅ 强化 post_toot 的 schema 描述（明确参数、默认值、用途）——已由 post_toot 工具发现增强解决（2026-08-24）
- ✅ system prompt 增加约束——已解决：改为「发 Mastodon 用 post_toot 工具，参数 text 是正文」
- 工具发现：模型搜 "post_toot" 时应能快速定位到 meta_tools.py 而非误搜 mastodon.py（仍未单独处理，可选优化）

### 状态
✅ 已由 post_toot 工具发现增强解决（2026-08-24）：`schemas.py` 明确 post_toot 为「已注册的 Mastodon 发帖工具，直接调用即可」，`system_prompt.py` 改为「发 Mastodon 用 post_toot 工具」。根因（schema 描述模糊 + prompt 措辞弱）已消除，模型不再需要绕道 run_command。

## runtime 结构层清理第 1 轮（2026-08-23）

只做 3 项，不碰 P3，不推远程，未 commit。

### 1. 删除 run_legacy 废弃链
- 删除 `Runtime.run_legacy`、`Runtime._update_phase_from_result`、`self.phase`、`self._confirm_fn`/`self._abort_fn`
- 删除 `forge/agent_state.py`（AgentPhase 仅被上述死代码使用）
- `make_tools` 移除 `confirm_fn`/`abort_fn` 闭包，返回值从 3 元组改为单值 `tools`；同步更新全部 ~20 处测试调用点（`tools, _, _ = make_tools(...)` → `tools = make_tools(...)`）
- **纠正原判断**：`forge/confirmation.py` 未整删——`is_confirm`/`is_cancel` 仍被生产路径 `Runtime._handle_plan_reply`（计划确认流程）使用，只删了无人引用的 `extract_confirmation`。原「同属废弃链」结论对 confirmation.py 不完全成立。

### 2. WorkingSet 持久化（.forge/task_state.json）
- `WorkingSet` 新增 `to_dict()` / `from_dict()`，序列化 8 字段：goal / constraints / files_read / files_edited / open_hypotheses / pending_verify / verify_targets / failure_context
- runtime 新增 `_save_task_state` / `_load_task_state`（纯 JSON 全量存取，不做增量同步/版本迁移）
- `_run_conversation` 启动时加载上次 task_state（goal 以本次 task 为准，其余字段恢复）；每次 `update_from_tool` 后保存
- JSON 缺失/损坏/非对象 → 静默返回 None 重建，不阻塞启动
- 新增测试 3 个（`test_p1_working_set.py`）：roundtrip / 坏输入容错 / 存取+损坏处理

### 3. create_object 从 `_EDIT_TOOLS` 移除
- `_EDIT_TOOLS` 去掉 `create_object`（纯 World 对象操作，非文件编辑），消除其成功后触发一次空 checkpoint 的问题
- 影响面：`update_from_tool` 本就因无 path 不记 files_edited；唯一行为变化是 `mutation_pending` 不再被 create_object 置位（P2-3 checkpoint 触发判定）

### 验证
- 全量 pytest：**395 passed，10 skipped**（基线 392 + 新增 3，无回归）

### 真实遗留问题
1. `confirmation.py` 仍保留 `is_confirm`/`is_cancel`（计划确认流程使用），并非整文件废弃。
2. ~~`verify_map` / `failure_target` 两个内部字段未持久化~~ → 已由 **P0 Verify State Continuity** 解决：二者写入 `task_state.json`，恢复后 guard / 精确清账语义连续。
3. 每次工具调用（含只读 read_file 等）都触发一次写盘；JSON 很小成本可忽略，严格可优化为「状态变化时才写」。
4. `task_state.json` 位于 `.forge/`（已 gitignore），不会污染 git。

---

## P3-2 + P3-3：local_tools 拆模块 + get_call_chain 优化（2026-08-23）

只做这两项，不做 P3-4/5/6，不推远程，未 commit。

### P3-2：local_tools.py 拆模块（1526 → 48 行）
- `forge/tools/local_tools.py` 从 1526 行拆成 8 个文件，工具函数体一字不改：
  - `_common.py`（37 行）：`LOG_PATH` / `MAX_OUTPUT_CHARS` / `_log` / `_truncate` / `_truncate_head`
  - `read_tools.py`（494 行）：read_file / read_function / read_files / read_file_with_lines / preview_line_mutation / get_symbol_line_range / extract_code_skeleton / summarize_file / get_repo_map / list_files / get_context_budget
  - `search_tools.py`（322 行）：search_code / glob_files / find_symbol_definition / get_call_chain / rebuild_symbol_index / search_history / inspect_last_intent
  - `git_tools.py`（154 行）：git_diff / git_log / git_status_enhanced / get_diff_summary / read_git_version
  - `test_tools.py`（260 行）：run_test_structured / run_type_check / run_single_test / run_diagnostics / list_tests
  - `world_tools.py`（120 行）：world_info / list_world_objects / get_world_object / list_world_links / resolve_path_object
  - `meta_tools.py`（264 行）：todo_write / todo_list / web_fetch / project_memory / session_changes / run_command / post_toot / delete_toot
- `local_tools.py` 保留 `make_local_tools` 组装入口，再导出 `_log` / `_truncate` / `MAX_OUTPUT_CHARS` 等以保持 `test_truncate_keeps_tail` 兼容。
- 各子模块的 factory（`make_read_tools(workspace)` 等）仍以闭包方式定义工具，`workspace` / `world_runtime` / `_todo_items` 通过闭包捕获，函数体不变。
- 返回 key 集合不变（41 个），schema 不变。

### P3-3：get_call_chain 优化
- 原实现：两遍全仓 AST 遍历（第一遍找定义+收集被调用者，第二遍找调用者），每次调用都重新 parse 每个 .py。
- 优化后：
  1. 定义定位改走 `forge.core.symbol_index.lookup_symbol`（只认 `kind in ("function","class")`，与原实现只匹配 ClassDef/FunctionDef/AsyncFunctionDef 对齐，避免误报变量），命中则跳过第一遍全仓遍历。
  2. 被调用者只解析定义所在文件（去重）。
  3. 调用者仍需一遍全仓遍历（符号索引不存调用点），但用模块级 `_AST_CACHE`（键含 mtime_ns+size，上限 256，超限整体清空）缓存已解析 AST，跨调用复用。
  4. 索引未命中时回退到原全仓找定义逻辑（复用已缓存 AST，几乎零额外成本）。
- 返回 display / payload 格式完全不变。
- 性能（300 个 .py 合成项目，索引已预热）：原实现 ≈1966ms → 优化后冷 ≈1584ms、热（AST 缓存命中）≈1447ms，稳态约 26% 提升；定义定位从全仓遍历降为索引 O(1) 查询。

### 验证
- 全量 pytest：**395 passed，10 skipped**（与基线一致，无回归）。

### 真实遗留问题
1. `get_call_chain` 的「调用者」仍需一遍全仓遍历——`symbol_index` 只索引符号定义、不索引调用点（call sites）。若要进一步提速，需扩展 `symbol_index` 存反向调用图（每个名字→调用它的位置），属更大改动。**状态：暂缓**。
2. `_AST_CACHE` 是进程内内存缓存，不落盘；长会话内多次调用同一/不同符号可复用，但新进程首次调用仍要冷启动一次全仓 parse（索引本身已落盘 `.forge/symbols.json`，可省「找定义」一遍）。
3. ~~`make_local_tools` 的 `safe_mode` 参数自始未被任何工具使用（拆分前即是如此），本轮未动，仅保留签名兼容。~~ → 已删除（2026-08-24）：`safe_mode` 死参数及 `security.py` 的 `ALLOWED_COMMAND_PATTERNS` / `is_allowed_command` 白名单一并移除（白名单从未实现，黑名单仍是唯一策略）。

## P3-4 + P3-5 + P3-6：detect 缓存 + undo 文档化 + openai_compat 429 重试（2026-08-23）

只做这三项，不推远程，未 commit。

### P3-4：Sync detect 缓存
- 问题：detect() 每次全量扫 receipt 历史（get_receipts_since(0)），receipt 量级增长后变慢。
- 方案：SyncLayer 记录 `last_detect_version` + 缓存上次 `SyncReport`。detect() 先取廉价磁盘侧
  指纹（git HEAD + 已知文件实时 hash + git untracked 状态）+ 同步水位（disk_synced_version /
  last_known_commit / last_known_file_hashes）构成完整缓存键 `_detect_cache_key`；键不变时直接
  返回上次报告的浅拷贝（`_clone_report`），跳过全量 receipt 扫描。
- 三态判定语义完全不变：任一输入（World version / 同步水位 / 磁盘侧）变化即失效重算。
  特别处理了 sync() 后第二次 detect 的场景——external_sync 重算 hash、mark_disk_synced 推进
  水位都会改变缓存键，不会误命中旧报告（这些字段都进了键）。
- git status 失败（GitError）仍返回 WORLD_UNAVAILABLE：指纹里的 git status 调用单独守卫，
  保持 P0-batch5「git status 故障不得伪装 IN_SYNC」语义。
- 测试：新增 3 个（缓存命中不重复扫 receipt / World 前进失效 / 磁盘编辑失效）。

### P3-5：undo_last_tx 语义文档化
- 在 tx_shadow.py（模块 docstring + undo_last docstring）与 intent_tools.py（undo_last_tx
  docstring）里明确文档化：undo 只从 shadow 恢复磁盘，不回滚 World 账本、不写 external_sync
  receipt、不推进/回退 disk_synced_version；undo 后 World 账本可能仍较新，以磁盘 read 为准，
  待 veritasd 恢复后 forge_sync 对账。
- display 提示已足够清晰（无需改）：undo_last_tx 的 body 已写「mode=file_shadow_revert；World
  账本可能仍较新，以磁盘 read 为准」，kv 标注 world=may_lag / disk=restored。
- 不实现「undo 写 external_sync receipt」（超范围，维持 MVP 语义）。
- 测试：新增 2 个（undo_last 纯磁盘不写 receipt / undo_last_tx display 明示 may_lag）。

### P3-6：openai_compat 429 重试
- 对齐 deepseek.py 重试策略：指数退避 `2**attempt`（1/2/4s），最多 3 次。
- 判定更精确：`_is_retryable_error` 优先看 `status_code`，仅 429 与 5xx 重试；其它 4xx
  （400/401/404/422...）立即抛出不重试。无 status_code 的传输层错误（连接/超时）按网络抖动处理。
- 响应格式完全不变（retry 只包住 create 调用，成功路径照旧走 choices 解析）。
- 测试：新增 4 个（429 重试后成功 / 5xx 重试后成功 / 其它 4xx 不重试 / 3 次耗尽后抛出）。

### 验证
- 全量 pytest：**404 passed，10 skipped**（基线 395 + 新增 9，无回归）。

### 真实遗留问题
1. detect 缓存是进程内内存缓存，不落盘；新进程首次 detect 仍要冷启动一次全量 receipt 扫描。
2. detect() 里 `world_version` 与报告分支里的 `_world_version()` 是两次 `get_version()` 调用；
  理论上 World 在两调用间前进会短暂不一致（下一次 detect 即重算自愈），非本轮范围。
3. openai_compat 对无 status_code 的传输层错误（连接/超时）靠字符串关键词判定，与 deepseek 一致，
   仍属启发式；若需更强可改判 `openai.APIConnectionError` / `APITimeoutError` 具体类型。

## P0：Verify State Continuity（2026-08-24）

### 问题
P1-5 / P1-6 之后 `verify_map` 已成为行为状态（精确清账 + pending_verify guard），
但 `WorkingSet.to_dict()` 未持久化 path→target 关联。Runtime 重启后
`verify_targets` 仍在、`verify_map` 为空 → guard 失去 pending 路径集合，
跨会话控制语义与进程内不一致。

### 实现
- **canonical 验证关联事实**：`verify_map`（path → set/list of targets）
- `to_dict`：序列化 `verify_map`（targets 为 sorted list）与 `failure_target`
- `from_dict`：容错解析 map；有 map 时 `_sync_verify_views_from_map()` 同步
  `pending_verify` / `verify_targets` 表达层；无 map 的旧 task_state 保持原字段、不伪造
- 空 target 集不写入 JSON；非法 path/target 丢弃
- `failure_target` 一并持久化（与已持久化的 `failure_context` 配套，供成功测试精确清失败上下文）

### 测试
- `tests/test_p0_verify_state_continuity.py`：跨 Runtime 恢复 map、guard 一致、
  精确清账、清账后再恢复、坏输入、旧快照无 map
- 既有 `test_p1_verify_*` / `test_p1_working_set` roundtrip 同步更新

### 明确不再成立的旧描述
- ~~`verify_map` / `failure_target` 仅进程内内存，跨 Runtime 不恢复~~ → 已可恢复

---

## post_toot 工具发现增强（2026-08-24）

- 问题：发嘟任务里模型绕过已注册的 `post_toot` 工具，改用 `run_command` 直接调 MastodonClient 发帖——根因是 schema 描述模糊（"可选，非强制"）且 system prompt 称"可发 Mastodon（可选"。
- 修复：`schemas.py` 强化 post_toot 描述为「已注册的 Mastodon 发帖工具，需要发嘟时直接调用，无需搜索实现或手写脚本」，明确 text 参数 / visibility 默认值与所需环境变量；`system_prompt.py` 改为「发 Mastodon 用 post_toot 工具，参数 text 是正文」。纯正向引导，无 run_command 黑名单、无安全改动。
- 测试：421 passed（无新增用例，test_tool_surface_alignment 保持绿）。

## safe_mode 死参数 + ALLOWED_COMMAND_PATTERNS 白名单清理（2026-08-24）

- 问题：`make_tools` / `make_local_tools` 的 `safe_mode` 参数自始未被任何工具使用；`security.py` 的 `ALLOWED_COMMAND_PATTERNS` + `is_allowed_command` 白名单从未实现（仅黑名单生效）。
- 修复：删除 `safe_mode` 死参数与白名单常量/函数，docstring 同步（黑名单仍是唯一策略）。
- 测试：421 passed（纯删除，无回归）。

## path_map 重建静默失败修复（2026-08-24）

- 问题：`WorldRuntime._rebuild_path_map` 里 `get_receipts_since(0)` 抛异常时静默 return，path_map 重建失败不可观测。
- 修复：失败时置 `_path_map_degraded = True` 并打 stderr 日志（`[world] path_map rebuild failed...`），不再静默。
- 测试：新增 1 个回归用例（断言 degraded 置位 + stderr 含关键词）；全量 422 passed。

## DEGRADED/WARN 统一语义 + path_map guard 消费端（2026-08-24）

- 问题：工具成功后的附属副作用失败只有 `side_effect_warnings` 一种可观测，无法区分「关键状态不可信（DEGRADED）」与「附属失败（WARN）」，且 path_map 降级无机器消费端。
- 修复：`ToolResult.payload` 新增 `degraded` / `warnings` 机器字段（旧 `side_effect_warnings` 保留兼容）；intent_tools 按标签分类——path_map / sync_watermark / task_state 记 DEGRADED，cache / record 记 WARN；`WorldRuntime` 新增 `degraded_components` + `is_degraded` / `mark_degraded` / `clear_degraded`；`Runtime._guard_path_map_degraded` 在 path_map 不可信时拦截文件 mutation（forge_sync / undo_last_tx 放行）。`_save_task_state` 暂按 WARN 处理（无消费端，已文档化）。
- 测试：422 passed。

## 安全两修：run_single_test shell=False + 工具输出统一 sanitizer（2026-08-25）
- 问题：DSH 漏洞审计后发现 Forge 虽无远程控制面，但 run_single_test 用 shell=True 拼路径有注入隐患；工具输出进 LLM 上下文前未经统一 sanitizer。
- 修复：run_single_test 改 argv 列表 + shell=False；Runtime/subagent 在 tool message 进 LLM 前统一 sanitize_and_redact。
- 测试：新增 2 个测试文件（shell 注入 + sanitizer 边界）；全量 431 passed。

## SECURITY_BOUNDARY.md 建立（2026-08-25）
- 明确定义：Forge 当前无远程控制面（本地 CLI 入口）；run_command 是本地用户信任边界内的能力，非远程 RCE；黑名单/sanitizer 是 Safety Guard 非 Security Boundary；未来 A2A 前必须认证 + schema 分离 + run_command 不对远程开放。
- 文档位置：docs/SECURITY_BOUNDARY.md

## 终端呈现层 Batch 1：工具输出摘要（2026-08-25）
- 问题：工具输出截断保留头部 18 行，错误信息在尾部被砍掉。
- 修复：summarize_tool_display 纯函数——短输出全文；长成功头 4 + 尾 12 + 省略标记；失败尾部优先。
- 测试：8 个单元测试；全量 439 passed。

## 终端呈现层 Batch 2：TerminalPresenter + pager（2026-08-25）
- 问题：last 命令全文 print 刷屏，无分页。
- 修复：TerminalPresenter 类 + page_text 最小 pager（Enter 下一页 / b 上一页 / q 退出，无 raw mode）；dp.py 不再内嵌 print 闭包。
- 测试：23 个 presenter/pager 测试；全量 454 passed。

## 终端呈现层 Batch 3：heartbeat（2026-08-25）
- 问题：长工具执行（60s）期间终端只有 "..." 无反馈。
- 修复：threading.Timer 每 10s 打 "running... Ns"（新行不打 \r）；token 防止 END 后过期 tick；可注入 timer_factory 便于单测。
- 测试：8 个 ManualClock 确定性测试；全量 464 passed。

## 终端呈现层 Batch 4：assistant streaming（2026-08-25）
- 问题：模型回复等完整 response 才显示，长等待像卡死。
- 修复：BaseAdapter 新增 send_stream 契约（默认 fallback send）；stream_util 解析 OpenAI chunk；Runtime 优先 stream + on_text_delta 钩子；Presenter 增量显示 FORGE>。
- 测试：8 个 streaming 测试；全量 472 passed。

## 终端呈现层 Batch 5：ANSI 磷光色彩语义（2026-08-25）
- 问题：emoji（🔧✅❌🤖⚠️）与 Termux IBM 绿磷皮肤不协调，颜色无语义。
- 修复：terminal_color.py 真彩色调色板（PHOSPHOR/OSCILLOSCOPE/ALARM/TUBE_BLUE/AMBER）+ paint() 强制 RESET；去掉 emoji 改用 [name] OK/FAIL、FORGE>、WARN:；正文不染色。
- 测试：10 个 color 测试；全量 482 passed。

## 终端标准文档 + 文档治理（2026-08-25）
- docs/standards/terminal_presentation.md：TerminalPresenter/pager/heartbeat/streaming 行为契约（MUST/MUST NOT）。
- docs/standards/terminal_color_semantics.md：颜色语义规范（调色板、映射、RESET/泄漏、emoji 政策）。
- docs/standards/README.md：标准注册表 + 权威层级（Standard > Constitution > Contract > Security > Architecture > Stance > Audit）+ 变更规则（改行为先改标准）。
- 9 份 docs/ 根目录文档加 Type/Authority/Status/Scope 元数据。

## STATUS.md 早期历史恢复（2026-08-25）
- 问题：昨日整理 STATUS.md 时误删了 8-05 到 8-20 的全部逐日记录。
- 处理：从 Git 历史提炼早期脉络摘要（7-14 起点 → 8-05 安全 → 8-06 Projection/Recovery → 8-07 架构收敛 → 8-08 规划 → 8-09 修 bug → 8-12 语义 → 8-16 身份边界），插入头部；完整逐日记录仍在 Git 历史可查（git log --follow STATUS.md）。
- 教训：整理历史文档 ≠ 删除过时内容。过时应标注（~~删除线~~或"历史：已被 XX 推翻"），原文保留；流水账可压缩但事实不丢。


## Project Review / 事实检索闭合（2026-08-25）
- 问题：回顾类问题 ad-hoc 并联 git + STATUS + memory + history，证据链不闭合。
- 修复：project_review 统一 FACT/EVIDENCE/CONTEXT/CONFLICTS；last_test_result 持久化；PROJECT_REVIEW_CONTRACT；system_prompt 回顾规则；相关工具描述降权。
- 测试：tests/test_project_review.py 13 passed；全量 495 passed。

## 同步工具阶段可见性修复（2026-08-25 晚）
- 问题：forge_sync 被归入 MUTATION_TOOL_DECLARATIONS，导致 Planning 阶段模型看不到该工具；用户输入 forge_sync 后模型退化用 run_command 手动执行 SyncLayer.sync()（34 次工具调用）。
- 根因：工具分类只有 READ_ONLY / MUTATION 两类，forge_sync 的「安全对账」语义既非纯只读也非破坏性修改，放错分类导致 Planning 阶段不可见。
- 修复：新增 RECONCILIATION_TOOL_DECLARATIONS（对账类），forge_sync 移入；Planning 与 Execution 阶段 schemas 均包含 RECONCILIATION。验证后 forge_sync 首次调用即直接命中，工具调用数 34 → 1。
- 附带修复：Planning 阶段模型「用文字说提交计划但未调用 submit_plan」问题——强化 _PLANNING_INSTRUCTION + _run_conversation 加 require_plan 精确纠偏（仅当文字含「计划/步骤/方案」等关键词且未调 submit_plan 时触发一次）。
- 第二个附带修复：submit_plan 计划正文不显示——模型流式输出 content 后，show_assistant() 因「已流式过」跳过最终返回值，导致计划正文丢失。修复：show_assistant() 加 force 参数；Runtime 加 _last_response_needs_display 标记；submit_plan 时强制显示计划正文，普通问答保持不重复。
- 测试：新增 tests/test_reconciliation_schema_exposure.py（2 个双阶段可见性用例）；更新 test_p2_2_sync_system_hint.py / test_tool_surface_alignment.py 迁移到 RECONCILIATION 工具面；全量 499 passed。

## Pending Action Gate：Phase 状态机替换（2026-08-26）
- 问题：Planning/Execution 两阶段用 schema 裁剪控制工具可见性，所有 mutation（含 post_toot）在确认前对模型不可见；确认=整段计划+进入 Execution 后永久获得全部写权限。
- 修复：删除 _run_planning/_run_execution/_handle_plan_reply 与 _pending_plan/_pending_task/_submitted_plan；新增 PendingAction 冻结精确 tool_call 快照（tool+args+tool_call_id），用户确认后 Runtime 直接执行快照不重问模型；工具始终完整可见；权限轴只有 READ/WRITE，策略桶 WRITE_CONFIRM/WRITE_RECOVERY/WRITE_BLOCKED；用户确认与安全 Guard 正交（确认不能绕过 Guard）。
- 测试：新增 test_pending_action_gate.py；全量 501 passed。

## forge_sync 独立 FORGE_SYNC 策略（2026-08-26）
- 问题：Pending Action Gate 上线时 forge_sync 归入 WRITE_RECOVERY 桶，detect 也会被当作写操作冻结 PendingAction，语义不符。
- 修复：新增独立 FORGE_SYNC 策略与 _forge_sync_observe_or_pending：detect 只读观察不冻结；仅 FAST_FORWARD 才冻结 PendingAction 确认推进；CONFLICT STOP。
- 测试：新增 test_forge_sync_gate.py（9 个用例）；test_pending_action_gate.py 断言更新（forge_sync == FORGE_SYNC，移出 WRITE_RECOVERY）；全量 510 passed。

## Agent ABI v1：主 AI ↔ 子 AI 硬契约实现（2026-08-26）

- 背景：真实使用审计发现 8 个问题，归并为 5 个根因。核心不是缺功能，而是主 AI 与子 AI 之间只有自然语言 task，没有硬边界。
- 方案：参照 Forge → Veritas 的 WRI 思路，建立 Agent ABI，把主 AI 的授权和子 AI 的回报变成可追溯、可拒绝、可验收的硬接口。
- 契约文档：docs/AGENT_ABI.md（v1.3，含冻结增补裁定与工具映射补充裁定）。

### 第一步：ToolCallRecord append-only 日志
- 问题：子 AI 的工具调用没有不可变记录，Evidence 无法独立反查。
- 修复：新建 forge/tool_call_record.py。每次真实工具调用前分配 tool_call_id，执行后写入 .forge/tool_call_records.jsonl。write_record 失败不影响工具结果；get_record 供验收反查。
- 测试：全量 510 passed。

### 第二步：工具映射表 + 命令前缀白名单
- 问题：约束判定层需要把 tool_name 确定性翻译成 action / path / command_class，不能靠模型判断。
- 修复：新建 forge/tool_action_map.py（39 个工具全部登记，apply_patch 标记 __UNPARSEABLE_PATH__）与 forge/command_class_prefixes.py（长前缀优先，未登记 unknown）。
- 测试：全量 510 passed。

### 第三步：constraint_enforcer 约束判定
- 问题：not_allowed / scope.paths 如果只进 prompt，就是假硬约束。
- 修复：新建 forge/constraint_enforcer.py。未登记工具默认拒绝；not_allowed 黑名单先于 scope.paths 白名单；machine 拒 / advisory 记违规放行；command_class unknown 且有约束时拒；path_in_scope 修复 src2 不匹配 src。
- 测试：全量 510 passed。

### 第四步：stop_when 硬终止
- 问题：子 AI 没有显式停止信号，stop_when 藏在自然语言里。
- 修复：subagent.py 新增 STOP_WHEN: met/not_met 行信号。met 时丢弃本轮 tool_calls 直接产出结论；缺失或非法值按 not_met；strip_stop_when 去掉控制行。
- 测试：全量 510 passed。

### 第五步：AgentResult 组装 + AgentTask 契约
- 问题：子 AI 直接返回自然语言结论，主 AI 无法区分“真完成”和“看起来完成”。
- 修复：新建 forge/agent_abi.py。AgentTask 五字段；AgentResult status 仅 done/blocked/need_decision 且由机器组装；Evidence 必须绑定真实 tool_call_id；done_when v1 代理 = stop_when_met 且 evidence 非空；run_subagent 签名从 task: str 改成 AgentTask，返回 AgentResult；constraint_enforcer 接入子循环；runtime spawn_subagent 最小胶合。
- 测试：更新 test_subagent_and_ux.py / test_subagent_conclusion.py；全量 510 passed。

### 第六步：主 AI 验收 precheck + verify_tool_call
- 问题：主 AI 没有独立反查工具，验收只能信 conclusion 文本。
- 修复：agent_abi.py 新增 lookup_evidence_records / precheck_agent_result（磁盘再验，done 证据全失效降级 blocked）；新增只读工具 verify_tool_call，只返回 record 原始字段不返回 claim；system_prompt 加验收纪律；tool_action_map 登记 verify_tool_call。
- 测试：全量 510 passed。

### 补测试
- 问题：六个新模块没有专门测试，只有旧测试适配。
- 修复：新增 7 个测试文件共 43 个用例，覆盖 ToolCallRecord、工具映射、命令前缀、约束判定、Agent ABI 组装/precheck、stop_when、verify_tool_call。
- 测试：新增 43 passed；全量 553 passed。

### 当前状态
- Agent ABI v1 六步实现全部完成并推送。
- 剩余未解决：终端体验问题（问题 8）——run_command 捕获模式导致动画/实时输出无法展示给用户，待设计 PTY/交互终端方案。已记入 TODO.md，优先级 P0。

---

## Phase 1：Control Plane / Execution Plane 工具面隔离（2026-08-27）

- 问题：主 AI 仍然拥有全部执行工具（read_file / write_file / run_command / str_replace 等），可以从工具面直接干工程活。主从分工只有 prompt 约束，没有代码层隔离。
- 修复：schemas.py 新增 CONTROL_PLANE_TOOL_DECLARATIONS / EXECUTION_PLANE_TOOL_DECLARATIONS 两组常量。主 AI 默认 schema 收缩为 CONTROL（spawn_subagent / verify_tool_call / todo_write / todo_list / submit_plan）；子 AI 通过 spawn_subagent 获得完整 EXECUTION（READ + MUTATION + RECONCILIATION）。全量 registry 不动，真正隔离的是角色过滤后的 LLM 可见面。
- 验证：CONTROL ∩ EXECUTION = ∅；主 AI 看不到任何执行工具；全量 562 passed。
- 文档：docs/MAIN_AGENT_BEHAVIOR.md v1 冻结（主 AI 七职责、默认派发、直接处理四例外、澄清原则、验收关系、控制/执行面边界）。

## Phase 2：Execution Pause / Write Confirmation 迁移到子 Runtime（2026-08-27）

- 问题：写确认机制还在主循环的 Pending Action Gate 里，子 AI 的写操作没有确认门禁。
- 修复：run_subagent 内 enforce 之后挂 Execution Gate。Gate 分类：ALLOW（只读）/ PAUSE（需确认写）。confirm_fn 由 spawn_subagent 闭包注入，用户确认后在同一个子 Runtime 栈帧内继续执行，不重问模型。confirm_fn=None + PAUSE → blocked / confirmation_unavailable；用户拒绝 → blocked / user_denied_write。
- Layer B：执行前后状态快照。ALLOW 路径产生未授权变化 → blocked / unauthorized_world_change。已确认写入不误杀。
- AgentResult.status 枚举不变，exit_kind 只映射到 blocked。
- 主 AI 不持有 pending、不转发、不 resume。用户确认路径：用户 → CLI → confirm_fn → 恢复子 Runtime，不经过主 AI。
- 验证：全量 576 passed。

## Phase 2.1：command classification 收敛 + compound 绕过修复（2026-08-27）

- 问题：enforcer 和 execution_gate 各自实现 resolve_command_class，split-brain。且 compound 正则漏了 \n / & / > / < 等 shell 控制结构。run_command 用 shell=True，换行/重定向可绕过前缀分类，未授权写成功。
- 修复：command_class_prefixes.py 成为唯一 classification source of truth。新增 is_compound_shell_command（覆盖 && || ; | & \n \r > >> < << ` $(）。enforcer 和 Gate 都调共享实现。compound → COMMAND_CLASS_UNKNOWN → enforcer 有约束则 deny，Gate 则 PAUSE。
- 验证：绕过向量全部被阻断；只读命令仍 ALLOW；全库仅一处 resolve_command_class 定义；全量 583 passed。

## Layer B VERIFY 类别 + 文档 v3（2026-08-27）

- 问题：run_test_structured / run_type_check 被当成只读工具放在 _ALWAYS_ALLOW_TOOLS，但它们实际会写 .pytest_cache / __pycache__ / .mypy_cache / .forge/last_test_result.json。Layer B 快照看不到这些路径，检测盲区。
- 修复：新增 VERIFY_TOOL_NAMES 和 VERIFY_SIDE_EFFECT_PATTERNS。VERIFY 仍 ALLOW，但 Layer B 对 VERIFY 做「实际变化 − 白名单副作用 = 剩余」，剩余非空才报 unauthorized。run_test_structured 写 .forge/last_test_result.json 合法；若它改 forge/runtime.py 仍会被抓住。
- 文档：MAIN_SUBAGENT_IMPLEMENTATION_DESIGN.md 升级 v3。新增 4.2.1「世界变化」定义（工程世界变化 vs 验证副作用）和 4.2.2 三类工具判定表（READ_ONLY / VERIFY / WRITE）。
- 验证：全量 589 passed。

## Phase 3：主 AI 行为契约落地（2026-08-27）

- 问题：system_prompt.py 还是旧身份，教主 AI 自己用 glob_files / str_replace / write_file 等执行工具。但工具面已经是 CONTROL ONLY，prompt 和现实冲突。
- 修复：重写 SYSTEM_INSTRUCTION。主 AI 是判断/控制层；工程任务默认 spawn_subagent；直接处理只有四例外（纯对话 / 已有事实总结 / 明确只分析 / 极简单无工具）；无法形成 goal / done_when / stop_when 必须先澄清；AgentResult 不得只信 conclusion，必须 verify_tool_call 反查 ToolCallRecord。
- 验证：prompt 不含任何执行工具名；spawn_subagent 是默认路径；全量 595 passed。

## Runtime Integration：确认终端独占 + 事件桥接 + 同步上下文（2026-08-27）

- 问题 1：子 AI 确认提示用 sys.stdin.readline()，和主循环 read_multiline_input 的 termios 输入冲突。确认提示后 heartbeat 继续刷 running...，看起来像没等用户就继续执行。
- 修复：Runtime 接受 confirm_provider，dp.py 的 cli_confirm 用 read_multiline_input 独占终端。TerminalPresenter 新增 begin_exclusive / end_exclusive / exclusive_terminal；确认期间 heartbeat / tool / assistant 全部暂停输出。
- 问题 2：子 AI 执行完全黑盒，看不到工具调用过程。
- 修复：run_subagent 接受 emit 回调，子循环在工具执行前后发 TOOL_CALL_START / TOOL_CALL_END。spawn_subagent 传 self.emit，CLI 的 TerminalPresenter 订阅同一 EventBus。子 AI 工具调用实时可见。
- 问题 3：主 AI 收到 forge_sync 时不知道当前同步状态，盲目 spawn。
- 修复：system_prompt 加「同步场景下的行为」规则。FAST_FORWARD → 构造带方向的 AgentTask；CONFLICT → 先让用户决策；WORLD_UNAVAILABLE → 不主动同步。
- 验证：真实运行 forge_sync 全链路通过（主 AI 说明状态 → spawn → 子 AI 执行 → 确认 → IN_SYNC → verify_tool_call 验收 → 解释）。全量 597 passed。

## 语言跟随规则（2026-08-27）

- 问题：用户英文输入，主 AI 用中文汇报。
- 修复：system_prompt 加「语言跟随」规则。面向用户的最终解释、澄清和汇报必须使用与用户输入相同的语言。
- 验证：全量 598 passed。

## 真实行为验证（2026-08-27）

### forge_sync 同步链路

- 输入：forge_sync
- 主 AI 先说明当前 FAST_FORWARD(Disk → World) 状态
- 确认块独占终端，无 running 串行
- 用户确认后子 AI 执行 forge_sync，返回 IN_SYNC
- 主 AI verify_tool_call 反查 ToolCallRecord，验收通过
- 向用户解释 FACT / INFERENCE 边界

### 测试套件验收链路

- 输入：英文「跑测试并总结稳定性」
- 主 AI 判断执行层任务，spawn 子 AI
- 子 AI 执行 git status / ls tests / pytest（两次完整运行 597 passed）
- 每个写命令都弹出确认
- 主 AI 对 4 个 tool_call_id 逐一 verify_tool_call
- 主 AI 明确说「597 这个数字来自子 AI conclusion，我只能验证 exit code=0」
- 汇报区分 FACT / INFERENCE，列出未验证边界

## TODO.md 更新（2026-08-27）

- 删除已解决条目
- 新增：旧 PendingAction 死代码清理（P3）、子 AI prompt 审查（P2）、子 AI 重复执行观察（P3）、行为验证扩展（P2）
- 保留：CONFLICT 行为契约（P0）、PTY 终端（P0）、glob_files 隐藏目录（P2）、旧测试门禁迁移（P2）

## 验收链修复：verify_subtask_evidence + 持久化（2026-08-27）

- 问题 1：主 AI 从渲染文本抄 tool_call_id 导致截断，verify 失败。
- 问题 2：_subagent_results 是内存表，进程重启后跨会话验收失败。
- 问题 3：run_command 的 ToolCallRecord 只有 returncode，没有 stdout，「598 passed」等具体数字无法独立验证。
- 修复 1：新增 verify_subtask_evidence(subtask_id)，主 AI 只传 subtask_id，Runtime 从结构化 AgentResult 取完整 tool_call_id 精确反查。verify_tool_call 保留为底层精确 primitive。
- 修复 2：新增 forge/subagent_results_store.py，_subagent_results 持久化到 .forge/subagent_results.jsonl（append-only，last-wins，坏行跳过，缺文件正常启动）。
- 修复 3：run_command payload 包含 stdout/stderr + stdout_truncated/stderr_truncated 标志。
- system_prompt 重写验收规则：优先 verify_subtask_evidence；主 AI 不复制 UUID；verify 失败 ≠ 工程任务失败；禁止因验收失败重新 spawn；returncode=0 不能推导具体数字；stdout_truncated 不能当完整证据。
- 验证：全量 618 passed。

## 端到端行为验证（2026-08-27）

### 跨会话验收验证

- 重启 Forge 后输入「Verify the evidence for subtask sub_6304c7f108f1」
- verify_subtask_evidence 直接返回 all_ok=True，证明 JSONL 持久化跨会话工作
- 主 AI 只传 subtask_id，没有手写 UUID
- 主 AI 诚实说明「没有任务的目标上下文，只验证证据链，不验证任务意图」

### 测试套件数字验收验证

- 输入英文「跑测试并精确报告 passed/failed 数字」
- 子 AI 第一次用 /tmp 路径写输出失败（Termux 无 /tmp），改为管道 tail 成功
- 子 AI 跑两次 pytest，分别拿到 618 passed in 31.66s 和 29.37s
- 主 AI 用 verify_subtask_evidence 验完整证据链
- 主 AI 发现第一条 record 的 stdout 是截断 preview，明确说「31.66s 不能独立确认」
- 主 AI 用第二条 record 的完整 stdout「618 passed in 29.37s」作为独立证据
- 英文汇报，符合语言跟随规则

### 最长函数分析验证

- 输入英文「找代码库最长的 3 个函数」
- 主 AI 判断需扫描代码库，spawn 子 AI
- 子 AI 用 AST 脚本统计 168 个 Python 文件、1497 个函数
- 结果：make_intent_tools（1233 行）、make_read_tools（476 行）、make_meta_tools（341 行）
- 子 AI 用 read_file 确认 docstring 和函数内容
- 子 AI 发现 forge_rain.py / matrix_termux.py 不存在（glob 0 匹配）
- 主 AI 验收：verify_subtask_evidence all_ok=True，11/11 evidence 有效
- 主 AI 从 stdout 原文引用前三名精确数据
- 主 AI 明确区分 FACT（函数名行数）和 INFERENCE（功能总结）
- 主 AI 发现 working-set 记忆说 forge_rain.py 存在，但 glob 证据说不存在，采信工具证据

## 关键行为确认

- 主 AI 全程没有调用任何执行工具
- 主 AI 优先使用 verify_subtask_evidence，不再手写 UUID
- 子 AI 全程在执行层工作
- 事实/推断分离在三个真实任务中一致执行
- 跨会话验收工作正常

## R1/R2/R3 架构闭合 + SyncDecision 真实闭环（2026-08-28）

### 背景

在完成三轮反审计后，确认 Forge 在「运行时生命周期」「同步决策」「AgentTask 机器边界」三根支柱上存在结构性缺口。审计收敛为最小原语 R1/R2/R3，并冻结不变量。

### R3：AgentTask Contract 入口接线

- 问题：spawn_subagent schema 只有 task 字符串，done_when/stop_when/not_allowed/scope 在生产入口不可达；constraints 恒为空。
- 修复：
  - schemas.py 暴露 goal/done_when/stop_when/not_allowed/scope/max_steps
  - runtime.py 提取 _build_agent_task_from_spawn_args 纯函数
  - spawn_subagent 闭包调用纯函数构造完整 AgentTask
  - not_allowed/scope 折叠进 constraints，由 constraint_enforcer 强制
- 测试：新增 tests/test_agent_task_contract_wiring.py；全量 630 passed。

### R1：RuntimeState 最小持久化

- 问题：运行时状态散落在栈帧、日志、WorkingSet，崩溃后无法判断「从哪里继续」。
- 修复：
  - 新增 forge/runtime_state.py：RuntimeState / Pending / Recovery / Store
  - 持久化 .forge/runtime_state.json，只含 phase / active_subtask_id / pending
  - recovery 不持久化，启动时由 phase + pending 推导
  - 损坏文件安全回退 .broken，对齐 SyncState 模式
- 测试：新增 tests/test_runtime_state.py；全量 640 passed。

### R2：SyncDecision 最小闭环

- 问题：CONFLICT/FAST_FORWARD 是用户决策点，但没有机器对象；Mutation Confirmation 被混同于同步方向决议。
- 修复：
  - 新增 forge/sync/decision.py：SyncDecision + SyncDecisionStore
  - sync_status() 检测到 CONFLICT/FAST_FORWARD 时打开 decision + RuntimeState.pending
  - Gate 在 pending.kind=sync_decision 时拒绝 mutation 和 forge_sync
  - resolve_sync_decision(direction) 完成决议并清除 pending
- 测试：新增 tests/test_sync_decision_r2.py；全量 650 passed。

### R2 真实闭环暴露的缺口与修复

- 缺口：resolve_sync_decision 只有 Runtime API，没有控制面工具，用户/主 AI 无法触达决策入口。
- 真实验证：forge_sync 被 Gate 拦截，子 AI 报告 blocked，主 AI 无法完成决议，闭环断裂。
- 修复：
  - 新增控制面工具 resolve_sync_decision(direction)
  - schema + tool_action_map + Runtime 注册 + system_prompt 行为规则
  - 主 AI 必须得到用户明确方向后才能调用；子 AI 不可见该工具
- 测试：更新相关测试；全量 652 passed。

### 真实行为验证：FAST_FORWARD SyncDecision 闭环

- 场景：FAST_FORWARD(Disk → World)
- 链路：
  1. 主 AI 说明方向唯一，请求用户确认
  2. 用户确认后主 AI 调用 resolve_sync_decision(direction=disk_to_world)
  3. pending 清除，status=decided
  4. 主 AI 派子 AI 执行 forge_sync
  5. forge_sync 弹 Mutation Confirmation，用户确认
  6. forge_sync 返回 IN_SYNC（world_version=115, disk_synced_version=115）
  7. 主 AI verify_subtask_evidence + verify_tool_call 验收通过
- 关键点：
  - SyncDecision 与 Mutation Confirmation 两层分离，各自独立确认
  - 主 AI 未自行决定方向，用户是最终授权者
  - pending 清除后 Gate 正确放行

### 当前架构状态

- R1 RuntimeState：最小持久化闭环 ✅
- R2 SyncDecision：最小决策闭环 + 真实验证 ✅
- R3 AgentTask Contract：入口接线 ✅
- 未完成：durable pause / 子循环恢复 / 全局 stop / 控制面状态查询工具
- 未完成产品能力：PTY 实时终端 / CLI -c / Termux 系统集成
- 未清理技术债：主循环旧 PendingAction / 旧测试迁移 / glob_files 隐藏目录

### 测试基线

- 全量：652 passed
- git diff --check：clean

## R1 phase 生命周期驱动修复（2026-08-29）

- 问题：R1 定义了 phase=RUNNING_SUBTASK 和 active_subtask_id，但 spawn_subagent 从未写入。字段定义存在，生命周期未驱动。
- 修复：
  - spawn 前：phase=DISPATCHING + active_subtask_id
  - 进入 run_subagent 前：phase=RUNNING_SUBTASK
  - append_ok 后：phase=IDLE + active_subtask_id=None
  - except 路径：复位 IDLE
- 测试：全量 652 passed。

## Durable Pause 设计与实现（2026-08-29）

### 设计定案

- 新增 docs/DURABLE_PAUSE_DESIGN.md，经三轮审计修正：
  - 第一轮：Claude 初版设计，发现 phase 未驱动的前置缺口
  - 第二轮：Grok 审计抓出两个硬伤——checkpoint 清除早于 AgentResult 落盘；resume 缺终态检查
  - 第三轮：修正时序不变量、死分支、事实摘要读取范围、原子写要求
- 最终裁定：INCONSISTENT 不删除只降级事实验证；checkpoint 永不覆盖终态 AgentResult；副作用只缓解不保证 exactly-once。

### 实现落地

- 新增 forge/subtask_checkpoint.py：SubtaskCheckpoint / Store / derive_subtask_recovery / 事实验证 / 事实摘要
- AgentTask.from_dict 对称构造
- list_records_for_subtask 全量只读
- run_subagent 在 Layer B 通过后写入 checkpoint
- spawn 闭包在 append_ok 后 clear checkpoint + 复位 phase
- resume_subtask / abort_subtask 控制面工具
- abort_subtask 补 append 失败保护：不 clear checkpoint

### 真实 smoke 验证

- 场景：发现残留 checkpoint（sub_b8933d2e20a8），RuntimeState=IDLE
- 派生结果：INCONSISTENT，事实校验通过
- 用户明确要求放弃后，abort_subtask 合成 blocked 结果、清理 checkpoint、复位 phase
- 落盘验证：runtime_state=IDLE、checkpoint 删除、subagent_results 有 blocked 终态

### 测试基线

- 全量：666 passed
- Durable Pause 专项：14 passed
- git diff --check：clean

## 架构主线完成

三根支柱 + 子 AI 恢复全部闭环：

- R1 RuntimeState：最小持久化 + phase 生命周期 ✅
- R2 SyncDecision：决策对象 + Gate + 真实闭环 ✅
- R3 AgentTask Contract：入口接线 + enforce ✅
- Durable Pause：断点 + 恢复 + 真实 smoke ✅

剩余为产品能力和技术债：

- 产品：PTY 实时终端 / CLI -c / Termux 系统集成
- 技术债：旧测试迁移 / 死代码清理 / glob_files 隐藏目录
- 行为验证：CONFLICT 场景 / 更多真实任务覆盖

## Forge Persona 定稿与接入（2026-08-29）

### 设计定案

- 新增 docs/FORGE_PERSONA.md：身份锚点 (•_•)、五条性格（诚实/可靠/有点脾气/皮实/不逢迎）、犯错处理、人格适用范围、颜文字情绪词库、说话风格、与用户的关系。
- 诚实条挂钩既有的 FACT/EVIDENCE/INFERENCE 区分（ToolCallRecord 验证 vs 子 AI 转述），不是新造概念。
- 有点脾气条明确划界：情绪是表达不是借口，不得作为拒绝执行/消极怠工的理由。
- 皮实条对应 CONFLICT 场景：遇到无法安全自动解决的冲突时停下来说明双方选择，等用户裁决，不擅自偏向。
- 人格适用范围限定在主循环对用户对话；子任务内部通信保持结构化，不带人格、不带表情。

### 实现落地

- forge/system_prompt.py：模块加载时读取 docs/FORGE_PERSONA.md，拼接到 SYSTEM_INSTRUCTION 末尾（FileNotFoundError 时静默跳过，不影响原有行为）。
- 优点：以后改人格只改 md 文件，不用碰 .py。

### 真实验证

- dp.py 实跑：连续三次问同一问题，模型在第三次正确使用 (¬_¬) 升级语气，同时明确说明"不是拒绝回答"，继续给出完整答案。触发点与"不当挡箭牌"两条规则均验证通过。

## 启动 Banner 与终端配色合规清理（2026-08-29）

### 设计定案

- Banner 内容：(•_•) + FORGE 标识 + 英文 tagline（"Dispatches work. Verifies results. No conclusions without evidence."），复用同一身份锚点，不另立新符号。
- 配色严格遵循 docs/standards/terminal_color_semantics.md：边框用 TUBE_BLUE（secondary chrome 语义），标识文字用 AMBER（assistant identity 语义），未引入任何新颜色。
- 宽度改为运行时读取 shutil.get_terminal_size()（40–76 列区间自适应），替换掉最初写死的固定列数，解决了边框在不同终端宽度下无法贴边的问题。

### Emoji 清理（terminal_color_semantics.md 第 11 条）

- 范围明确限定在 dp.py（本次改动的展示层入口），未涉及 mastodon.py（公开社交文案，不同场域）、sanitizer.py、intent_tools.py/read_tools.py（工具体内容）、auto_loop.py（独立批处理脚本，数十处 emoji），这几处是否清理留待单独决定。
- dp.py 内替换：🌍→[world]，⚒️/💬/📊 移除或换成纯文本（forge> 提示符、去掉与 runtime.py stderr 重复的 [stats] 行），💾/👋→OK:/FAIL:/再见。
- 修复过程中发现的历史遗留：dp.py 里 [stats] 本次工具调用 与 runtime.py:2255 的 [stats] tools=（stderr 流）内容重复，确认后者是诊断日志、保留不动，删除 dp.py 里视觉重复的一条。

### 测试基线

- 未新增自动化测试；本轮改动为纯展示层（dp.py 单文件），通过实机运行手动验证 banner 渲染、人格触发、emoji 替换效果。

## Human Intervention：主 AI → 人类升级通道（2026-08-30）

### 契约冻结

- 新增 docs/HUMAN_INTERVENTION_CONTRACT.md v1，按文档治理规则归类为 Contract（与 RUNTIME_STATE_CONTRACT 同级）。
- 核心语义：human_intervention 是主 AI 主动、可持久、用户直接裁决的任务级升级，不是 AI 拒绝权、不是 PendingAction、不是 Durable Pause。
- 复用 phase=AWAITING_USER + pending.kind=human_intervention，不新增 phase，不改 AgentResult 三值。

### Phase 1：最小闭环实现

- runtime_state.py：pending.kind 扩展为 sync_decision | human_intervention；derive_recovery 支持 human 恢复。
- schemas.py：request_human_intervention / resolve_human_intervention 控制面工具。
- runtime.py：打开/裁决 handler、turn boundary、Gate 拦截任务推进、ABORTED→IDLE、与 sync pending 互斥。
- 用户裁决机器解析 continue / modify / abort，不经过主 AI 自然语言翻译。
- modify 必填 user_note；abort 后 phase=ABORTED，下一条新任务输入才回 IDLE。
- 测试：tests/test_human_intervention.py 新增，全量 684 passed。

### Phase 2：决策流接入

- 触发窗口：必须已有 spawn_subagent + verify_subtask_evidence，分叉来自 need_decision 或 blocked 多路径证据；禁止零 spawn 编造 A/B。
- payload 自动写入 original_goal / source_subtask_ids / evidence_digest（不扩 API）。
- continue 恢复 WorkingSet.goal=original_goal，防止目标漂移。
- modify 在 original_goal + user_note 上重新规划，旧路径授权作废。
- abort 不继续执行。
- source_subtask_ids 只收集 need_decision / blocked，done 被排除。
- system_prompt 与 MAIN_AGENT_BEHAVIOR.md §9 同步行为契约。
- 测试：test_human_intervention.py 扩到 22 passed；全量 692 passed。

### 真实验证

- 模糊任务「改一个文件」：主 AI 正确澄清，未擅自修改。
- 升级裁决：主 AI 调用 request_human_intervention，turn boundary 生效，用户 modify 后旧授权作废并重新规划派发。
- 后续子任务执行 + verify_subtask_evidence 验收链路正常。

## 死代码清理 + Layer B 观察边界修复（2026-08-30）

### Git 确认死代码清理

- 问题：GIT_CONFIRM_COMMANDS + needs_git_confirmation 无活调用方；_PAUSE_CLASSES / _normalize_cmd / COMMAND_CLASS_UNKNOWN import 未使用。确认机制已统一为 command_class_prefixes → execution_gate。
- 修复：删除上述死代码。确认链路保持 command_class_prefixes → resolve_run_command_gate → classify_for_confirmation → subagent → PAUSE。
- 测试：全量 692 passed。

### Layer B 观察边界修复（run_command 盲区）

- 问题：原 _layer_b_snapshot 从 args 的 path/file/target/edits 提取快照路径。run_command 参数只有 cmd，before/after 恒为空，Layer B 对 shell 写工程文件失明。与 MAIN_SUBAGENT_IMPLEMENTATION_DESIGN.md §4.2「执行面工具调用前后取状态快照、不依赖 tool_name 名单」不一致。
- 修复：
  - 新增 forge/workspace_manifest.py：全 workspace metadata manifest（kind/mtime_ns/size/symlink target），排除 .git、cache、venv、.forge 等噪声目录。
  - 对称 diff：before.keys() | after.keys()，捕获新增、删除、修改。
  - run_subagent 执行前后改用 build_workspace_manifest，不再依赖 args 路径。
  - VERIFY 授权副作用继续用 changed - patterns 减法。
  - confirmed_write / recovery 行为不变。
- 测试：新增 tests/test_layer_b_workspace_manifest.py，含 run_command 改文件被 Layer B 捕获的集成测试。全量 703 passed。

## 文档治理：长期演进体系建立（2026-09-01）

### Forge Longevity Guide

- 新增 docs/FORGE_LONGEVITY.md：定义 Forge 的长期演进方向，区分「会过时的实现形式」和「不会过时的契约语义」。
- 四层连续性：契约 / 世界 / 证据 / 用户权威。
- 继承判定规则：未来声称 Forge successor 的系统，必须满足 5 条连续性要求 + 2 条声明要求。
- 核心原则：升格的是语义，不是实现形式。壳可以换，契约必须留。

### Core Contract Set 注册

- docs/standards/README.md 的 Contract / Interface 表格补全核心契约注册：
  - docs/AGENT_ABI.md
  - docs/RUNTIME_STATE_CONTRACT.md
  - docs/HUMAN_INTERVENTION_CONTRACT.md
  - docs/WORLD_DISK_SYNC.md
  - docs/world_runtime_interface.md
- 明确注册 ≠ 自动升格 Normative Standard。

### Core Contract Normative Audit

- 新增 docs/audits/CORE_CONTRACT_NORMATIVE_AUDIT.md：条款级审查五份核心契约，区分「具备 Normative 性质的语义」和「仍属实现形式的细节」。
- 结论：AGENT_ABI 的 Evidence/tool_call_id 绑定、WORLD_DISK_SYNC 的 CONFLICT MUST STOP、HUMAN_INTERVENTION 的用户权威、RUNTIME_STATE 的事实/决定/水位分离，具备升格资格；WRI 整份暂不升格。

### Normative Promotion Policy 建立

- 经 v1 → v2 → v2.1 三轮修订和两次模拟升格（Dry Run），最终落盘 docs/governance/NORMATIVE_PROMOTION_POLICY.md。
- 定义升格门槛 Gate A–F：行为语义、规范力、可验证、跨实现稳定、不可破坏核心、显式登记。
- 禁止 deferred conformity：代码不符合不得升格。
- Clause Package 只允许同一不变量闭环，禁止跨不变量打包。
- 模拟升格对象：WORLD_DISK_SYNC 的「CONFLICT 下不得自动推进 synchronization progress marker」，结论 Simulation PASS — Promotion Eligible, Not Promoted。
- 政策定位为 Governance Procedure，非 Normative Standard，不进 §4。


## P1-01 修复 + R2 组合测试 + glob_files + get_runtime_state（2026-09-01）

### P1-01：SyncDecision crash-window Gate 失明修复

- 问题：_maybe_open_sync_decision 先写 SyncDecision PENDING，再写 RuntimeState.pending。两步之间崩溃会导致 Gate 只认 RuntimeState.pending，短暂失明。
- 修复：
  - sync_decision_pending_blocks 增加 fail-closed 次信号：SD.status == PENDING 且 RS.pending 非 human_intervention 时仍 block。
  - 启动时 _reconcile_sync_decision_pending 对齐 RuntimeState.pending。
  - _maybe_open_sync_decision 先检查 HI，HI 存在时不创建新 PENDING decision。
  - resolve 保持先终态后清索引，避免反向 false allow。
- 测试：新增 crash-window / reconcile / stale artifact / clear failure / HI priority 等用例。
- 全量：715 passed

### R2 组合安全边界测试

- 新增 4 个组合测试：
  - HI pending × spawn/mutation 生产路径拦截；工具未调用；workspace 不变。
  - reconcile 后 sync_decision pending × mutation 仍关闭。
  - 主文件 + .tmp 双文件存在时 load 只认主文件。
  - clear 失败残留主文件时 pending 仍 blocked。
- 全量：719 passed

### glob_files 显式 .forge 可见

- 问题：显式查 .forge/... 仍被硬编码排除，工具输出与磁盘事实不一致。
- 修复：默认继续隐藏 .forge；pattern 显式以 .forge 或 .forge/ 开头时可见。
- 测试：新增宽 pattern 隐藏 + 显式可见用例。
- 全量：721 passed

### get_runtime_state 控制面只读工具

- 新增控制面只读工具 get_runtime_state。
- 返回 phase / active_subtask_id / pending 摘要 / recovery。
- pending 期间可调用；不写盘；不绕过 Gate。
- 工具内直接读已派生 recovery，不再重复 refresh_recovery 并吞异常。
- 全量：725 passed

### 文档治理

- TODO 删除已完成项：R2 组合测试、glob_files、get_runtime_state。
- TODO 新增「架构审计遗留」分组。
- 新增 Core Contract Normative Audit 记录。

## P1：主 AI MAIN_READ_ONLY + ToolCallRecord actor（2026-09-02）

### 问题
主 AI 被完全剥夺读权，导致派任务前盲猜、子 AI 被迫过度侦查、主 AI 无法形成准确判断。

### 方案
- 主 AI 恢复最小只读：
  read_file / read_function / glob_files / search_code /
  find_symbol_definition / get_repo_map / git_diff
- 主 AI 仍禁止 mutation / reconciliation / shell / test。
- 每次主 AI 真实只读调用产生 ToolCallRecord(actor="main")。
- 子 AI 写入 actor="subagent"，旧 JSONL 默认 subagent。
- main record 不得作为子任务 Evidence。
- 主循环增加 _main_tool_policy_denied 第二层硬拒绝。
- policy refusal 持久化到 self.conversation。

### 改动
- forge/tool_call_record.py：actor 字段 + 旧数据兼容
- forge/tools/schemas.py：MAIN_READ_ONLY_*
- forge/runtime.py：主循环 policy 拒绝 + main record + conversation 持久化
- forge/subagent.py：显式 actor="subagent"
- forge/agent_abi.py：Evidence 拒绝 actor=main
- forge/system_prompt.py：主 AI 可只读、不可 mutation
- tests：新增 test_main_read_tool_records.py；迁移旧契约测试

### 文档同步
- docs/MAIN_AGENT_BEHAVIOR.md
- docs/MAIN_SUBAGENT_IMPLEMENTATION_DESIGN.md
- docs/RUNTIME_STATE_CONTRACT.md
- docs/AGENT_ABI.md
- TODO.md 删除主 AI 决策权边界 P1

### 验证
- 全量：731 passed

## WRITE_CONFIRM 不可达路径标注 + 不变量测试（2026-09-02）

### 问题
架构审计发现 `PendingAction` 机制存在两条分支：forge_sync 专用（活跃）与通用
WRITE_CONFIRM（strategy == "WRITE_CONFIRM"，主循环 3604 行起）。P1 main read-only
落地后，`_main_tool_policy_denied` 在 strategy 判定之前已拦截所有
MUTATION_TOOL_NAMES | RECONCILIATION_TOOL_NAMES 工具并 continue，导致
WRITE_CONFIRM 分支在当前工具面下结构性不可达——不是死代码（无死引用），而是被
自身策略拒绝逻辑架空的休眠代码，属于「两套机制共存但只有一套真正工作」的认知负债。

### 处理
未删除代码（保留作为未来配置回退时的安全网），改为：
- `_WRITE_CONFIRM_TOOLS` 定义处加注释说明不可达原因与触发条件
- 新增 `test_write_confirm_tools_unreachable_from_main_loop`（tests/test_tool_plane_isolation.py），
  锁定「主 AI 可见工具集 与 _WRITE_CONFIRM_TOOLS 不相交」为显式契约；若未来误将
  mutation/reconciliation 工具加入 CONTROL_PLANE_TOOL_DECLARATIONS，该测试会立即报红。

### 结论
把隐含的、需要逐层读代码才能验证的架构事实，转成机器可验证的不变量测试，
而非缩减代码量。

### 测试
全量：732 passed（基线 731 + 新增 1，无回归）。


## P1：只读误确认 + 确认框语义 + Ctrl+C 软停止（2026-09-02）

### 问题
1. 子 AI 使用 ls/cat/head/grep 等只读 shell 命令侦查时，被 command_class 判为 unknown，进入确认流程，造成 confirmation fatigue。
2. 确认框只接受“确认/confirm/yes/y/ok”，其余输入（包括“停止”“last”）都视为拒绝写入，用户控制输入被错误归类为 user_denied_write。
3. 用户在运行中输入“停止”无效，Ctrl+C 会直接退出整个 Forge，缺少软停止路径。

### 处理
- forge/command_class_prefixes.py：
  增加安全只读命令前缀 ls/cat/head/tail/wc/grep/rg/file/stat/du/df/
  pwd/which/whereis/uname/whoami/id → read_only。
- rg --pre / --pre-glob 强制 unknown，防止外部 preprocessor 任意执行。
- find/env/xargs/sed/awk/python/bash/sh/node/less/date 不加入，保持 PAUSE。
- compound shell 仍然 unknown → PAUSE。
- forge/runtime.py：
  增加 stop_requested；run() 捕获 KeyboardInterrupt 后软停并返回 forge>；
  主循环 step/tool 边界检查 stop_requested；确认框 Ctrl+C 置位并上抛。
- forge/subagent.py：
  每个 step 前、每个 tool 前后检查 should_stop；
  Ctrl+C / stop 时返回 exit_kind="user_stop"。
- forge/agent_abi.py：
  增加 user_stop exit_kind → STATUS_BLOCKED + reason，与 user_denied_write 区分。

### 改动
- forge/command_class_prefixes.py
- forge/runtime.py
- forge/subagent.py
- forge/agent_abi.py
- tests/test_command_class_prefixes.py
- tests/test_command_classification_unified.py
- tests/test_user_stop.py

### 验证
- 新增相关测试 27 passed
- 全量：746 passed

### 已知限制
- 不支持运行中中文“停止”急停。
- 不强制中断正在执行的 shell / LLM 请求。
- STOP 后子 AI 已产生 evidence/上下文丢失，已记录为下一条 P1。


## 主从协作全链路验证（2026-09-03）

### 目的
验证主 AI 被 mutation policy 约束时，是否自然委托子 AI，而不是直接写入或放弃。

### 方法
让主 AI 在 forge/runtime.py 顶部 docstring 种一个明显错字，再修复。

### 结果
- 主 AI 未尝试直接 mutation。
- 先处理 pending sync_decision，主动 resolve_sync_decision(disk_to_world)。
- spawn_subagent 执行 forge_sync，并 verify_subtask_evidence 验收。
- 再 spawn_subagent 种错字，确认后 str_replace 成功。
- 再 spawn_subagent 修复错字，确认后 str_replace 成功。
- 主 AI 全程只读 + 委托，没有试图自己写文件。

### 结论
- mutation policy → 主 AI 委托子 AI 的闭环成立。
- 关闭 TODO：主 AI 被 mutation policy 拒绝后不会优雅 spawn_subagent。

### 暴露问题
- 改一行注释消耗 32 个工具，多次额外确认。
- 子 AI 仍偏好组合 shell 做只读侦查。
- sync_decision 处理路径过重，payload.basis 与 summary 不一致增加侦查成本。

### 2026-09-03 sync_decision stale PENDING 跨 basis 复用修复（a0c55da）

- 问题：`_maybe_open_sync_decision` 复用旧 PENDING 时只检查 `status == PENDING`，未检查 `basis == status`。导致 summary 用当前 detect status，payload.basis 用旧 decision.basis，三处数据不一致。实机中主 AI 看到 summary=FAST_FORWARD vs payload.basis=CONFLICT，被迫读源码核实，消耗大量工具调用。
- 根因：两个写入点用了不同来源变量。`summary = f"...basis={status}"` vs `payload["basis"] = decision.basis`。
- 修复：第一个 if 加 `existing.basis == status`；summary 改用 `decision.basis`；stale PENDING 被覆盖前打印 stderr supersession 日志（不新增持久化状态）。
- 验证：
  - 746 tests passed
  - 实机：旧 CONFLICT PENDING + 新 detect FAST_FORWARD → supersession 日志正确，get_runtime_state 三处一致，主 AI 从 32 tools 降到 12 tools
- 新发现：主 AI 在用户选 abort 时实际传了 world_to_disk（参数惯性），工具回显方向与口头声明不符。与 scope 双编码同类，归入"主 AI → tool ABI 参数构造可靠性"问题。待后续统一处理。

### 2026-09-03 resolve_sync_decision 方向不一致校验修复（018e6db）

- 问题：`resolve_sync_decision` 幂等分支在 decision 已非 PENDING 时直接返回已有决策，不校验传入 direction 与已存 decision.direction 是否一致。用户选 abort 时，若已有 decision 是 world_to_disk/decided，新方向被静默吞掉。
- 根因：幂等分支将「无决议对象」和「已决议但方向不同」两种情况混为一类处理，只检查状态类别不检查方向字段。与之前 stale PENDING 跨 basis 复用是同一形状的缺陷。
- 修复：幂等分支开头加校验，`decision.direction != direction` 时 raise ValueError。同方向重复调用保持幂等不变。新增 2 个回归测试锁定行为（方向不一致拒绝 + 同方向幂等）。
- 验证：748 tests passed（新增 test_resolve_sync_decision_direction_mismatch.py）。
- 关联发现：
  - 控制面工具（resolve_sync_decision / spawn_subagent / get_runtime_state）不在 tool_call_records 记录范围。MAIN_READ_ONLY_TOOL_NAMES 双重用途（schema 暴露 + 审计记录条件），需解耦后单独处理（P1 TODO）。
  - conversation_log.jsonl 只存叙述文本，不存工具调用原始参数。审计追溯依赖 tool_call_records，但控制面工具全部缺失。

### 2026-09-03 控制面工具审计集合落地（2bc9b29 + 3c7fe06）

- 问题：控制面工具（resolve_sync_decision / spawn_subagent / get_runtime_state）不在 tool_call_records 记录范围。主 AI 控制面操作无审计痕迹，abort 被吞成 world_to_disk 的事故无法从日志坐实真实参数。
- 根因：runtime.py:3730 用 MAIN_READ_ONLY_TOOL_NAMES 判断是否记录，该集合双重用途（schema 暴露 + 审计记录条件），控制面工具不在其中。
- 修复：
  - 新开 MAIN_AUDITED_TOOL_NAMES = MAIN_READ_ONLY_TOOL_NAMES | {resolve_sync_decision, spawn_subagent}，与 schema 暴露解耦
  - 主循环条件改用 MAIN_AUDITED_TOOL_NAMES
  - get_runtime_state 第一版不纳入（高频只读、低审计价值）
  - ToolCallRecord.subtask_id 保持 ""（不扩 schema）
- 测试：
  - 单元：集合成员 + 控制面工具未泄漏进 READ_ONLY 暴露
  - 集成：真实 Runtime 构造（Runtime(adapter, ws, MemoryStore())）走 _run_conversation → MAIN_AUDITED → _record_main_tool_call → tool_call_records.jsonl，覆盖 resolve_sync_decision 和 spawn_subagent
- 验证：751 tests passed
- 后续：get_runtime_state 是否纳入审计、ToolCallRecord schema 是否需要演进，留待需要时再决定

## 同步决策→执行→恢复全链路闭环（2026-09-03 ~ 09-04）

### 背景
R2 只做了"打开决策→用户 resolve→清除 pending"，但没有执行桥接。resolve(disk_to_world) 后 forge_sync 重新 detect 又 CONFLICT，决策从未被消费。

### 设计文档
- 新增 `docs/standards/sync_decision_reconciliation.md`：v1.1 + Closure Addendum + Phase A Closure Amendment。
- 核心边界：generation 冻结授权观察；resolve 只授权不执行；执行方向只来自 durable SyncDecision；verify 用 detect()==IN_SYNC；watermark 只在真正同步成功后推进。

### Phase A — 授权判定（commit 76f0c8f）
- `SyncDecision` 增加 `generation` 字段，冻结决策时的 World/Disk 观察
- `build_sync_decision_generation()` + `fingerprint_managed_disk()` 构造授权边界
- `classify_decision_applicability()` 返回 applicable / stale / already_in_sync / not_decided / legacy_no_generation
- `forge_sync()` 在 DECIDED 时先走协议分支，不直接调用 sync()

### Phase B — disk_to_world 执行器（commit 64e6c73）
- `SyncState.accept_disk_wins()` 单次 durable mutation 完成 baseline 重建 + watermark 推进到 generation.world_version
- `SyncLayer.apply_disk_to_world_decision()` preflight → accept_disk_wins → detect() verify
- watermark 永不超过 generation.world_version

### Phase C — world_to_disk 执行器（commit 7206d61）
- `SyncLayer.apply_world_to_disk_decision()` 逐笔 receipt：apply 成功才 mark_disk_synced
- watermark 严格 <= 最高成功物化的 receipt.version
- 执行前冻结 receipt sequence，发现 version > G.world_version 即 authorization_error 停止
- `SyncDecision` 增加 `mark_count` / `last_marked_version`；partial execution 后 classify 返回 PARTIAL_EXECUTION，禁止普通 supersede

### Phase D — 崩溃恢复（commit 1825b8e + ed09693）
- 新增 `forge/sync/attempt.py`：ReconcileAttemptStore + recover()
- `.forge/reconcile_attempt.json` durable 记录冻结 execution_receipts、expected_path_effects、next_receipt_index
- 核心顺序：expected effects durable → apply → mark → progress durable
- 恢复三窗口：apply 前（无副作用）、apply 后 mark 前（磁盘匹配则补 mark）、mark 后（续跑）
- 磁盘不匹配或含糊 → STOP，不 supersede，不自动继续
- `forge_sync()` 入口先跑 recover()，stopped 时返回 recovery_blocked

### 测试
- 全量 794 passed，1 skipped（端到端集成测试壳，待补真实实现）

### 结论
同步安全链从"resolve 后 forge_sync 又 CONFLICT"到"决策→授权→执行→验证→恢复"完整闭环。

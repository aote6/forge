# Forge 状态

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
2. `verify_map` / `failure_target` 两个内部字段未持久化：恢复后 pending_verify/verify_targets 在、但 path→target 关联丢失；broad 运行仍可清账，精确关联需重建（任务范围限定 8 字段，有意为之）。
3. 每次工具调用（含只读 read_file 等）都触发一次写盘；JSON 很小成本可忽略，严格可优化为「状态变化时才写」。
4. `task_state.json` 位于 `.forge/`（已 gitignore），不会污染 git。

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
- `verify_map` / `failure_target` 仅进程内内存，跨 Runtime 实例不恢复（同 P1-1 Working Set）
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

### 剩余工作
- 约 100+ 处宽泛 `except Exception` 可改为具体异常类型（如 `except OSError`、`except json.JSONDecodeError`）
- 需要逐处读上下文判断该用哪个具体异常
- 预计 3~5 轮才能完成

### 收益评估
- 生产行为不变，仅提高代码可读性和维护性
- 收益低于 P3-2（拆 local_tools）和 P3-6（429 重试）

### 决定
暂缓，等 P3 其他项完成后再单独处理。

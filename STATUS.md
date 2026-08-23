# Forge 状态

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

## P0-1/P0-2 投影失败检查（已修复）
- 根因：mutation 主路径在 `projections.project()` 后不检查 `success`，与
  `confirm_fn`（失败→ToolResult.fail）语义不一致；模型会把 disk=FAIL 当成功。
- 修复：`intent_tools.py` 新增 `_failed_projections` / `_projection_failure_result`；
  `_register_path`、`_write_content_to_world`、create_object/create_file/modify_file/
  edit_files_batch/delete_file/link_objects/unlink_objects/apply_patch 全部在投影失败时
  返回 `ToolResult.fail`（payload `projection_failed=True`），且失败时不写 path_map。
- 说明：World 事务已提交无法自动回滚；失败文案引导 `forge_sync` 对账。

## 已知技术债（未处理）
- 全库约 147 处 `except Exception`（非裸 `except:`；旧记录「47 处」已过时），
  建议按关键路径分批清理（local_tools / intent_tools / runtime / file_projection / sync_layer）
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
- `forge/agent_state.py` 和 `forge/confirmation.py` 标注 "kept for run_legacy"，同属废弃链

### 关联
- P0-2 修复时选择"对齐语义"而非删除旧路径，因为 `confirm_fn` 仍被 `run_legacy` 引用
- 后续可整串删除：`run_legacy` + `confirm_fn` / `abort_fn` + `agent_state.py` + `confirmation.py`，但不属于当前 P0 范畴

### 状态
- 未处理，记入工程债

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

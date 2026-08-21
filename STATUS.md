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

## 已知技术债（未处理）
- 全库47处裸 `except Exception:`（历史遗留为主），建议分批清理
- ~~`forge: tx=NN v=NN` 自动commit与人类feature commit混线~~ 已解决：
  自动事务提交已停用（git_projection.apply() 不再 commit），历史中的
  `forge: tx=` 提交已通过 filter-branch 全部移除
- veritas_kernel 侧：object_birth 收窄 pub(crate)、WAL截断恢复测试，完全未碰
- forge/intents/intent_tools.py:210 投影结果被静默丢弃：ProjectionManager.project()
  内部对每个 projection 的 apply() 包 try/except（base.py:89-121），失败不抛异常、
  返回 success=False 的 ProjectionResult；调用处未检查返回值，_register_path 照常执行，
  可能返回"世界里存在但磁盘无文件"的 oid（2026-08 只读确认，未改）
- forge/projections/base.py:122-133 永不执行的旧实现死代码（121 行已 return，未删）

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

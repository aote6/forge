# SYNC_DECISION_RECONCILIATION_DESIGN v1.1
# Protocol Refinement

Base: v1
Mode: refinement only — no new sync engine, no forge_sync(direction=...), no resolve-as-executor, no new RuntimeState.phase
Code anchors: SyncLayer.detect / _disk_advanced / sync / _forward_world_to_disk, SyncState.record_external_sync / mark_disk_synced, FileProjection.apply, SyncDecision / SyncDecisionStore

## A. Disk Observation / Generation

### A.1 What Disk observation means in current detect()

Fact (code): There is no DiskSnapshot type. Disk side progress is inferred by:

- git_head_commit(project_root) → current HEAD
- last_known_file_hashes vs live hash_file(path) for already known paths (_disk_advanced)
- git_status_porcelain_untracked_all + _forge_known_paths → external untracked

So a Disk observation in Forge is not a full workspace tree snapshot. It is the same inputs detect() already uses to decide disk_advanced / CONFLICT / FF.

Semantic definition (v1.1):

Disk observation = the tuple of values that, together with World version / receipts beyond watermark, fully determine the SyncReport that was shown when the decision was opened.

It is an immutable freeze of detect inputs/outputs at decision time, not a parallel scanner.

### A.2 Minimal immutable facts in generation

Store only what detect() already computed or trivially re-reads the same way:

Field: basis
Source: SyncReport.status
Role: CONFLICT / FF_*

Field: conflict_kind
Source: SyncReport.conflict_kind
Role: content vs untracked_external

Field: world_version
Source: report
Role: World tip at decide time

Field: disk_synced_version
Source: report / SyncState
Role: watermark at decide time

Field: known_commit
Source: report
Role: last_known_commit baseline

Field: disk_commit
Source: report
Role: HEAD at decide time

Field: divergent_paths
Source: report (sorted copy)
Role: paths detect listed

Field: path_hash_fingerprint
Source: derived from same known-path hashes detect used
Role: working-tree content without inventing new scan

path_hash_fingerprint (design choice, minimal):

fingerprint = hash(sorted((path, hash_file(path) or "MISSING") for path in sorted(set(divergent_paths) union keys(last_known_file_hashes))))

- Reuses hash_file + known path set already central to _disk_advanced.
- Does not require hashing the entire repo or building a second index.
- Untracked paths: include sorted divergent_paths list in fingerprint when conflict_kind=untracked_external (content may be omitted if not in known set — see B).

Optional but recommended: store report_detail truncated string for UX only; not used for applicability.

### A.3 Covering uncommitted working-tree edits

Fact: _disk_advanced already treats hash drift on known paths as disk advanced, independent of whether HEAD moved.

Generation rule:

- disk_commit catches committed tip movement.
- path_hash_fingerprint catches uncommitted edits on known/divergent paths.

Both are required. HEAD-only generation would miss dirty tree; hash-only would miss pure commit/metadata moves with identical blobs (rare but possible). Together they match detect's notion of disk change.

### A.4 Roles: Git HEAD vs working-tree hashes

Signal: Git HEAD (disk_commit)
Role in detect / generation: Ancestry / tip identity; did the repo tip move since known_commit

Signal: Working-tree hashes
Role in detect / generation: Content identity of Forge-known (and listed divergent) files; did bytes change

Neither is sufficient alone for applicability. Sync metadata (last_known_*) is the baseline; live HEAD + live hashes are the observation.

### A.5 Do not build a second detect engine

Rule: Applicability recomputes by calling SyncLayer.detect() once, then comparing the new SyncReport (+ the same fingerprint formula on current disk) to generation.

Forbidden:

- Separate generation scanner with different path sets or hash rules
- Caching a private full-tree manifest

Allowed:

- Pure functions: fingerprint_from_report_and_state(report, sync_state) using existing hash_file / report fields

If fingerprint formula cannot be reproduced from report+state alone, that is a bug in what we stored, not a reason to add a new engine.

### A.6 Stale after user edits post-decision

On every forge_sync before mutation:

1. report = detect()
2. Recompute path_hash_fingerprint with current hashes
3. Applicable iff:

   - decision status == decided
   - report.status == generation.basis
   - conflict_kind equal (if set)
   - report.world_version == generation.world_version
   - report.disk_synced_version == generation.disk_synced_version
   - report.disk_commit == generation.disk_commit
   - report.known_commit == generation.known_commit
   - current path_hash_fingerprint == generation.path_hash_fingerprint
   - sorted divergent_paths equal (or fingerprint already includes them)

Any mismatch → stale: do not execute; supersede decision; open new PENDING with new generation from this report.

Design choice: v1.1 uses exact equality on the above (not world_version >=). Safer; matches decision binds one observation.

## B. disk_to_world

### B.1 Scope declaration

disk_to_world is workspace-wide authority (aligned with existing record_external_sync()).

Fact: record_external_sync recomputes hashes for all keys in last_known_file_hashes and sets last_known_commit to current HEAD. disk_synced_version is a single workspace-level integer.

Therefore CONFLICT disk_to_world must not pretend to be path-local watermark surgery; that would invent a path-level watermark model Forge does not have.

### B.2 Precise workspace-wide semantics

User meaning: For sync purposes, current Disk/Git observation is the content authority for this workspace; do not project outstanding World file receipts onto disk.

(1) Which World receipts are superseded?

All file-touching forge_tool receipts with version > disk_synced_version (the set _world_file_receipts_beyond(s) would return at decision time / apply time), for projection purposes only.

Supersede means: after successful disk_to_world, detect() must no longer treat them as world_advanced pending materialization.

Mechanism: advance disk_synced_version to the world_version frozen in generation (see below).

(2) Why may disk_synced_version advance?

Normative reading of Rule A + user authority:

- Rule A forbids advancing watermark when projection claimed success without disk write.
- disk_to_world is the opposite authorization: user rejects projecting those receipts; disk bytes are kept.
- Advancing watermark then means: sync layer will not attempt to apply these receipts, not these receipts were applied.

Legitimacy condition (all required):

- DECIDED direction=disk_to_world
- generation still applicable
- executor completed metadata update successfully
- subsequent detect() == IN_SYNC before clear

Design choice (explicit): Under CONFLICT disk_to_world only, advancing disk_synced_version to generation.world_version without FileProjection.apply is allowed and is the intentional supersede signal. This must be recorded in last_sync.source distinctly (e.g. user_reconcile_disk_wins) so it is never confused with forge_tool projection success.

(3) How last_known_file_hashes is rebuilt

Reuse record_external_sync(recompute_hashes=True) semantics:

- For every path currently in last_known_file_hashes: rehash; drop if missing.
- Update last_known_commit to current HEAD.
- Do not auto-add arbitrary untracked paths to known set (see untracked boundary).

Design choice: After supersede, known set remains previously known paths, refreshed to disk bytes — same as today's external sync, not a full git ls-files import.

(4) How World history is preserved

- No deletion of receipts, transactions, or World objects.
- Veritas history unchanged.
- Only SyncState projection cursor (disk_synced_version) and disk baselines move.

(5) Why this is not delete World history

Operation: Delete / rewrite World WAL
Effect: Forbidden; not done

Operation: Advance sync watermark + refresh disk baselines
Effect: Done

Operation: Stop future auto-apply of superseded receipts
Effect: Done

Receipts remain queryable historical facts (source=forge_tool). Sync metadata records user chose disk over projecting them.

### B.3 untracked / deleted / renamed boundaries

Case: Known path deleted on disk
disk_to_world behavior: record_external_sync drops hash entry when hash_file is None — path leaves known set

Case: Known path content changed
disk_to_world behavior: Hash refreshed

Case: Renames
disk_to_world behavior: No rename engine; appears as missing old path + possibly untracked new path

Case: Untracked external (conflict_kind=untracked_external)
disk_to_world behavior: Not cleared by workspace-wide disk_to_world alone (v1.1): still not in known set; detect may still CONFLICT. Directed success requires either user removes/ignores outside Forge, or a future explicit accept-untracked control. Design choice: do not treat disk_to_world as ingest all untracked.

Case: content_divergence only
disk_to_world behavior: disk_to_world may reach IN_SYNC after metadata supersede + hash refresh

## C. world_to_disk crash recovery

### C.1 Minimal durable attempt fields (on SyncDecision JSON)

No new RuntimeState.phase. Extend the existing decision document:

attempt:
  state: none | in_progress | failed
  direction: world_to_disk   (copy; must match decision.direction)
  started_at: float
  last_error: str | null
  last_receipt_version_attempted: int | null
  last_receipt_version_marked: int | null   (last version for which mark_disk_synced returned)

generation != attempt: generation freezes observation; attempt tracks execution progress claims.

### C.2 Receipt-level crash window (fact)

FileProjection.apply then mark_disk_synced are not one atomic FS+SyncState transaction.

Windows:

1. apply not started
2. apply wrote some/all files, mark_disk_synced not called
3. mark_disk_synced done for receipt N, next receipt not started
4. all receipts done, verify/clear not done

### C.3 Recovery algorithm (disk is truth; attempt is hint)

On forge_sync with DECIDED world_to_disk and attempt.state in {in_progress, failed, none}:

1. report = detect()
2. if report.status == IN_SYNC: clear decision; return ok
3. if not applicable(generation, report, fingerprint):
     if any disk mutation may have occurred (attempt.in_progress or last_marked set):
        → HARD STOP: stale after partial mutation (see D)
     else:
        → supersede → new PENDING
4. applicable: resume from watermark, not from attempt boasting
   s = SyncState.disk_synced_version
   receipts = file receipts with version > s (existing _world_file_receipts_beyond)
5. for each receipt in order:
     a. Re-check applicability cheaply OR at least disk_commit+fingerprint still match generation
        (if fail → D partial stale)
     b. Under CONFLICT authorization, apply even if disk diverged from known baseline
        (overrides FF-only guard in _receipt_conflicts_with_disk)
     c. On apply failure → attempt.failed; stop; no mark for this receipt
     d. On apply success → mark_disk_synced(version, written, deleted)  (only now)
     e. Update attempt.last_receipt_version_marked = version (durable)
6. report2 = detect()
7. if IN_SYNC → clear decision
   else → attempt.failed; retain decision

### C.4 Disk written, watermark not advanced

Recovery truth: compare receipt's intended paths to current disk (hash / existence) vs receipt payload via existing FileProjection.prepare / apply idempotency.

Policy (design choice):

- Prefer idempotent re-apply of the same receipt if watermark still < receipt.version.
- If re-apply is safe and content already matches, mark_disk_synced may run without further byte changes.
- If disk matches neither old baseline nor receipt (user edited during crash): treat as stale after partial mutation — do not mark; do not clear; re-decide.

Do not trust attempt.last_receipt_version_attempted alone to skip work or to mark.

### C.5 When mark_disk_synced is allowed

Only if:

- FileProjection.apply returned success for that receipt in this process attempt, or
- recovery proved disk already matches that receipt's effects and generation still applicable

Never mark because attempt says we tried.

### C.6 Partial apply continue

- Watermark already reflects successfully marked receipts.
- Next forge_sync continues with version > disk_synced_version only.
- That is the primary resume mechanism; attempt fields are diagnostics + in_progress latch for UX/recovery messaging.

## D. Stale during execution

Stage: Before first mutation
Definition: applicable fails; attempt.state==none and last_receipt_version_marked is null; disk_to_world not started
Action: Supersede decision; new PENDING; no side effects

Stage: After partial mutation
Definition: applicable fails but disk_synced_version > generation.disk_synced_version or disk hashes diverged from generation fingerprint or some receipts marked
Action: Do not auto-continue opposite direction. Retain decision as attempt.failed with explicit recovery message; do not clear; do not open a silent new PENDING that hides partial apply. Operator/Main AI must present: partial world_to_disk progress exists; user re-decides with new generation after detect(). Design choice: force new decision_id; old generation never executes again

Stage: All mutations done, before verify
Definition: applies+marks done; detect not yet IN_SYNC or clear not done
Action: Run detect(); if IN_SYNC → clear; if not → failed (incomplete model or external change); keep decision

Stage: After verify IN_SYNC, before clear
Definition: detect would be IN_SYNC; decision file still DECIDED
Action: Next forge_sync/startup: detect IN_SYNC → clear decision; idempotent

disk_to_world partiality: prefer single critical section (in-memory then atomic sync_state.json replace, already tmp+replace). If process dies mid-write, load may fail or load old state — re-run disk_to_world only if still applicable; else stale before mutation effectively.

## E. Protocol invariants (additive, necessary only)

Carry forward v1 I1–I14. Emphasize:

I15 generation is immutable observation binding; attempt is mutable progress claim; never use attempt as Disk truth.

I16 DECIDED authorization is scoped to one generation; it is not a standing write capability past stale/IN_SYNC/abort.

I17 workspace-wide disk_to_world supersedes projection of pending file receipts via watermark + baseline refresh; it does not delete or rewrite World history.

I18 Success for clearing a decision is only detect() == IN_SYNC (or abort with zero mutation).

I19 Recovery decisions after crash prefer live Disk observation + disk_synced_version over attempt fields.

I20 resolve_sync_decision never calls FileProjection.apply, record_external_sync, or mark_disk_synced.

## F. Phase boundaries (revised)

Phase: A — Authorization / applicability protocol
In scope: generation fields on open/resolve; applicable() using detect()+fingerprint; forge_sync branch: DECIDED+applicable → call stub that returns structured not implemented or only handles already IN_SYNC → clear; stale → re-PENDING; abort semantics; Gate unchanged; tests that prove no mutation on DECIDED without executor
Explicitly out of scope: Any real CONFLICT apply; watermark advance for disk wins; conflict overwrite; attempt recovery

Phase: B — disk_to_world executor
In scope: workspace-wide supersede via record_external_sync + authorized disk_synced_version → generation.world_version; last_sync.source distinction; verify+clear; content_divergence tests
Explicitly out of scope: world_to_disk overwrite; untracked auto-ingest; crash attempt state machine

Phase: C — world_to_disk executor
In scope: authorized path past _receipt_conflicts_with_disk for this generation; sequential apply+mark; verify+clear; partial failure leaves watermark at last good receipt
Explicitly out of scope: Full crash matrix / attempt durability (minimal attempt.state=in_progress allowed as hook only)

Phase: D — crash recovery
In scope: durable attempt fields; resume from watermark; disk-truth recovery for apply-without-mark; stale-after-partial policy; tests for all D rows
Explicitly out of scope: New RuntimeState phases; FS transactions

Phase A must not sneak reconciliation semantics:
DECIDED+applicable in Phase A may only (1) clear if detect already IN_SYNC, or (2) return a stable error reconciliation_not_implemented without touching SyncState or disk. That keeps protocol tests honest.

## Mapping to existing symbols (checklist)

Design concept: Observation
Existing anchor: SyncLayer.detect → SyncReport

Design concept: Disk baseline compare
Existing anchor: _disk_advanced, last_known_file_hashes, hash_file

Design concept: HEAD
Existing anchor: git_head_commit

Design concept: Untracked
Existing anchor: _external_untracked_paths

Design concept: Decision durable
Existing anchor: SyncDecision / SyncDecisionStore

Design concept: Open / resolve
Existing anchor: Runtime._maybe_open_sync_decision, resolve_sync_decision

Design concept: Tool entry
Existing anchor: forge_sync in tools/__init__.py

Design concept: FF disk wins today
Existing anchor: record_external_sync

Design concept: FF world wins today
Existing anchor: _forward_world_to_disk + FileProjection.apply + mark_disk_synced

Design concept: Watermark
Existing anchor: SyncState.disk_synced_version

## Design choices vs facts (summary)

Item: No DiskSnapshot; loose baseline compare
Kind: Fact

Item: disk_to_world workspace-wide via record_external_sync style
Kind: Fact + extended

Item: apply vs mark_disk_synced non-atomic
Kind: Fact

Item: generation field set + fingerprint formula
Kind: Design choice

Item: exact equality applicability
Kind: Design choice

Item: watermark advance on CONFLICT disk_to_world without apply
Kind: Design choice (authorized supersede)

Item: untracked not auto-ingested by disk_to_world
Kind: Design choice

Item: stale after partial → hard stop + new decision
Kind: Design choice

Item: attempt fields on SyncDecision JSON
Kind: Design choice

Item: no new RuntimeState.phase
Kind: Constraint satisfied

v1.1 bottom line: Generation is a frozen detect-compatible Disk+World observation, not a new snapshot subsystem. disk_to_world stays workspace-wide projection supersede with explicit watermark legitimacy and intact World history. world_to_disk recovery is watermark + live disk, with attempt as durable latch only. Phases keep protocol pure before any CONFLICT mutation.

## Closure Addendum

### 1. detect() 与 generation observation 的关系
（对照 v1.1 A.1 / A.5 / A.6）

generation 是对授权边界的冻结观察，不是对 detect() / _disk_advanced() 控制流的逐行复刻。_disk_advanced() 在 HEAD 变化时直接 True, [] 而不再扫 hash，属于 detect 的短路实现；generation 仍应同时冻结 disk_commit 与 managed 路径的 path_hash_fingerprint，因此它可以是 detect 所用信号的超集。applicability 的第一步仍是调用 detect() 得到当前 SyncReport，再用 generation 做授权是否仍绑定同一观察的判定，而不是要求两条路径在每种短路下输出完全同一中间值。

### 2. workspace-wide stale 边界
（对照 v1.1 A.2 / A.6 / B.1）

任意 managed 路径（last_known_file_hashes 与 generation 纳入的 divergent 集合）在决策后的内容变化一律使 generation stale，这是有意选择，不是过拟合。disk_to_world 在现有模型下是 workspace-wide 权威（与 record_external_sync / 单一 workspace watermark 一致），故授权绑定的是整份 managed Disk baseline，而不是单文件局部许可证。用户在 resolve 之后又改任一 managed 文件，必须重新 detect() 并重新决策。

### 3. 唯一 projection cursor
（对照 v1.1 C.1 / C.3 / C.6 / E I15）

SyncState.disk_synced_version 是唯一 durable 的 projection cursor，也是 resume 下一条 receipt 的唯一依据。attempt.last_receipt_version_marked（及同类 attempt 字段）只作诊断与 in_progress 闩锁，不得作为第二套 watermark 参与是否 apply、是否 mark、是否 clear 的决策。恢复与续跑一律以实际 disk_synced_version + 当前 Disk observation 为准。

### 4. partial mutation 后的授权边界
（对照 v1.1 D partial mutation / C.3 / E I16）

一旦某次 attempt 已对 Disk 产生过 mutation（含已 mark_disk_synced 的部分 receipt，或已写盘但未 mark 的窗口），该 generation 上的授权即告终结，不得用同一 generation 再次执行。恢复后的下一次合法执行必须来自新的 detect() 观察、新的 generation、新的 decision_id；旧 DECIDED 只可失败保留或被显式 supersede，不可静默重入。

### 5. 授权范围不可扩大
（对照 v1.1 A.6 exact equality / B.2 watermark / C.3 中途再检查 / E I16–I17）

generation 内的 world_version、disk 观察（含 disk_commit 与 path fingerprint）、paths 集合与 direction 在 attempt 开始后全部 immutable。执行过程中新出现的 World receipt、新 path、更高 world_version 均不在当前授权内，不得被自动纳入本次 apply/supersede。disk_synced_version 在任何方向下都不得推进到超过 generation.world_version；超出即属协议违规，必须停并转为重新决策。

## Phase A Closure Amendment

### C1. Generation 不是原子 DiskSnapshot

generation 由 opening report 与 opening-time managed-path fingerprint 组成，是授权边界的冻结观察，不是新的原子 DiskSnapshot 子系统。detect() 产出 report 与 build_sync_decision_generation() 计算 fingerprint 之间可能存在时间差，磁盘可能发生变化，这不构成协议缺陷；该变化会在后续 classify_decision_applicability() 重新 detect 并重算当前 fingerprint 时被捕获为 stale。

### C2. Applicability 的当前 fingerprint 必须基于当前 path set

classify_decision_applicability() 重算 fingerprint 时，必须使用当前观察得到的路径集合：

current_paths = sorted(set(current_report.divergent_paths) ∪ set(current_sync_state.last_known_file_hashes.keys()))

不得只对 generation.divergent_paths 或 generation 当时记录的路径集合重算 hash。使用旧路径集合会让新出现的 managed path 逃过 stale 检查，违反授权范围不可扩大原则。

### C3. Stale supersede 必须单次原子写入

DECIDED 被判定 stale 后，禁止先 clear(old) 再 save(new)。必须：

1. 基于当前 report 构造新的 PENDING SyncDecision，包含新 generation 和新 decision_id；
2. 通过 SyncDecisionStore.save(new_pending) 单次原子替换旧 DECIDED。

任何实现不得出现 clear 与 save 之间的空窗状态。旧 DECIDED 只可被新 PENDING 原子覆盖，或保留在审计历史中，不得以半删除状态存在。

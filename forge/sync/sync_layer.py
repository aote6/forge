"""SyncLayer — World ↔ Disk/Git 三态同步判定与安全推进。

契约 §3：判定必须同时考虑
- Git commit ancestry（HEAD 是否变化）
- working tree / 文件 hash
- World sync metadata（disk_synced_version + receipts）
- 外部新建、Forge 从未跟踪的 untracked 文件（缺口 #1）

产出四态：
- IN_SYNC
- FAST_FORWARD_DISK_TO_WORLD
- FAST_FORWARD_WORLD_TO_DISK
- CONFLICT
（非 Git 仓库 → NOT_A_GIT_REPO，契约 §决策 5：不做无 Git 的第二套状态机）

CONFLICT 通过 conflict_kind 区分：
- content_divergence：已知文件内容/HEAD 双方分叉
- untracked_external：工作区出现 Forge 从未跟踪的新建文件

契约 §4：任何未解决分叉 MUST STOP；禁止 skip → success → advance。
契约 §7：`forge sync` = 检测 → 依状态安全推进 / 报告冲突 → 更新元数据 → 记录同步事实。

性能备注（P3-4 已落地）：
detect() 在 World version 未变、且磁盘侧（git HEAD / 已知文件 hash /
git untracked 状态）与同步水位均未变化时，直接复用上一次 SyncReport，
不再全量 get_receipts_since(0)。仅当上述任一输入变化时才触发全量重算，
三态判定语义与无缓存时完全一致（见 detect() 内 `_detect_cache_key`）。
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from forge.sync.git_utils import (
    git_diff,
    git_head_commit,
    git_status_porcelain_untracked_all,
    hash_file,
    is_git_repo,
)
from forge.sync.state import SyncState

# ── canonical status tokens ────────────────────────────────────

IN_SYNC = "IN_SYNC"
FAST_FORWARD_DISK_TO_WORLD = "FAST_FORWARD_DISK_TO_WORLD"
FAST_FORWARD_WORLD_TO_DISK = "FAST_FORWARD_WORLD_TO_DISK"
CONFLICT = "CONFLICT"
NOT_A_GIT_REPO = "NOT_A_GIT_REPO"
WORLD_UNAVAILABLE = "WORLD_UNAVAILABLE"

# CONFLICT 子类（展示用；处理逻辑同为 STOP）
CONFLICT_CONTENT = "content_divergence"
CONFLICT_UNTRACKED_EXTERNAL = "untracked_external"


@dataclass
class SyncReport:
    """一次同步判定/推进的结构化结果。"""

    status: str
    world_version: Optional[int] = None
    disk_commit: str = ""
    known_commit: str = ""
    disk_synced_version: int = 0
    world_advanced: bool = False
    disk_advanced: bool = False
    divergent_paths: list[str] = field(default_factory=list)
    diff_hint: str = ""
    detail: str = ""
    # CONFLICT 时区分原因；非 CONFLICT 为 None
    conflict_kind: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "world_version": self.world_version,
            "disk_commit": self.disk_commit,
            "known_commit": self.known_commit,
            "disk_synced_version": self.disk_synced_version,
            "world_advanced": self.world_advanced,
            "disk_advanced": self.disk_advanced,
            "divergent_paths": list(self.divergent_paths),
            "diff_hint": self.diff_hint,
            "detail": self.detail,
            "conflict_kind": self.conflict_kind,
        }

    def format(self) -> str:
        lines = [f"sync_status: {self.status}"]
        if self.conflict_kind:
            lines.append(f"  conflict_kind={self.conflict_kind}")
        if self.world_version is not None:
            lines.append(f"  world_version={self.world_version}")
        lines.append(
            f"  disk_synced_version={self.disk_synced_version}"
            f"  known_commit={self.known_commit[:12] or '(none)'}"
            f"  disk_commit={self.disk_commit[:12] or '(none)'}"
        )
        if self.detail:
            lines.append(f"  {self.detail}")
        if self.divergent_paths:
            if self.conflict_kind == CONFLICT_UNTRACKED_EXTERNAL:
                lines.append("  external untracked files (Forge 从未跟踪):")
            else:
                lines.append("  divergent files:")
            for p in self.divergent_paths[:20]:
                lines.append(f"    - {p}")
        if self.diff_hint:
            lines.append("  diff:\n" + self.diff_hint)
        return "\n".join(lines)


def _touches_files(delta) -> bool:
    """receipt 是否涉及磁盘文件（有 memory_written 或 objects_deleted）。"""
    if delta is None:
        return False
    return bool(getattr(delta, "memory_written", None)) or bool(
        getattr(delta, "objects_deleted", None)
    )


def _path_from_memory_write(w) -> Optional[str]:
    """从 memory_written 条目解出 state_id=0 的路径字符串。"""
    if isinstance(w, dict):
        sid = w.get("state_id")
        val = w.get("value_hex")
    else:
        try:
            sid, val = w[0], w[1]
        except (TypeError, IndexError, ValueError):
            return None
    if sid != 0 or val is None:
        return None
    try:
        if isinstance(val, str):
            return bytes.fromhex(val).decode("utf-8")
        return bytes(val).decode("utf-8")
    except Exception:
        try:
            return str(val)
        except Exception as e:
            print(f"[sync] _path_from_memory_write fallback failed: {e}", file=sys.stderr)
            return None


class SyncLayer:
    """World / Disk / Git 三态同步。

    `file_projection` 仅用于 FAST_FORWARD(World → Disk) 时把已确认安全的
    World receipt 物化到磁盘；它不是"从 World 恢复缺失文件"的通用机制。
    """

    def __init__(
        self,
        project_root: str,
        world_runtime,
        sync_state: SyncState | None = None,
        file_projection=None,
    ):
        self.project_root = str(project_root)
        self._world = world_runtime
        self._state = sync_state or SyncState(self.project_root)
        self._file_projection = file_projection
        # P3-4：detect() 报告缓存。仅当 World version 与磁盘侧指纹均未变化时复用。
        self._last_detect_version: Optional[int] = None
        self._last_detect_key: Optional[tuple] = None
        self._last_detect_report: Optional[SyncReport] = None

    @property
    def state(self) -> SyncState:
        return self._state

    @property
    def last_detect_version(self) -> Optional[int]:
        """上一次 detect() 观察到的 World version（缓存命中时被复用）。"""
        return self._last_detect_version

    # ── world / disk observation ───────────────────────────────

    def _world_file_receipts_beyond(self, version: int) -> list:
        """source=forge_tool 且涉及文件的 receipt（version > `version`），升序。

        World 查询失败时异常向上抛，由 detect() 统一转 WORLD_UNAVAILABLE。
        不得在此处吞异常变 []——那会把“看不见”伪装成“没有”。
        """
        receipts = self._world.get_receipts_since(version)
        out = [
            r
            for r in receipts
            if getattr(r, "source", "forge_tool") == "forge_tool"
            and _touches_files(getattr(r, "delta", None))
        ]
        out.sort(key=lambda r: getattr(r, "version", 0))
        return out

    def _world_version(self) -> Optional[int]:
        """获取 World 当前版本；失败时向上抛，不在此处吞成 None。"""
        return self._world.get_version()

    def _current_hashes(self) -> dict[str, str | None]:
        known = self._state.last_known_file_hashes
        return {path: hash_file(path) for path in known}

    def _disk_advanced(self) -> tuple[bool, list[str]]:
        """磁盘/Git 是否相对已知状态前进（HEAD 变化或已知文件 hash 漂移）。"""
        c_known = self._state.last_known_commit
        c_disk = git_head_commit(self.project_root)

        if c_known and c_disk and c_known != c_disk:
            return True, []

        divergent: list[str] = []
        current = self._current_hashes()
        for path, known_hash in self._state.last_known_file_hashes.items():
            cur = current.get(path)
            if cur != known_hash:
                divergent.append(path)
        return bool(divergent), divergent

    def _forge_known_paths(self) -> set[str]:
        """Forge 认识的路径集合 = last_known_file_hashes ∪ receipt 历史路径。

        receipt 查询失败时异常向上抛（由 detect 转 WORLD_UNAVAILABLE），
        不得 fallback 成空集合——那会把“看不见历史”伪装成“没有任何已知路径”。

        性能：每次 detect 全量 get_receipts_since(0)。若 receipt 量级增长导致
        detect 变慢，是已知的下一步优化项（进程内缓存 / 增量索引），非本轮范围。
        """
        known: set[str] = set(self._state.last_known_file_hashes.keys())
        receipts = self._world.get_receipts_since(0)
        for r in receipts:
            if getattr(r, "source", "forge_tool") != "forge_tool":
                continue
            delta = getattr(r, "delta", None)
            if delta is None:
                continue
            for w in getattr(delta, "memory_written", None) or []:
                raw = _path_from_memory_write(w)
                if not raw:
                    continue
                try:
                    abs_p = str(Path(raw).expanduser().resolve())
                except Exception:
                    abs_p = raw
                known.add(abs_p)
                known.add(raw)
        return known

    def _external_untracked_paths(self) -> list[str]:
        """git status 中 ?? 且不在 forge_known_paths 的路径（绝对路径）。

        调用方须已确认 World 可达；本方法内 get_receipts 失败会向上抛。

        忽略：
        - 目录（git 对未跟踪目录只显示目录名，不代表“外部新建文件”）
        - `.forge/` 下路径（Forge 自身同步/备份元数据，不是项目源文件）
        """
        porcelain = git_status_porcelain_untracked_all(self.project_root)
        if not porcelain.strip():
            return []
        known = self._forge_known_paths()
        root = Path(self.project_root).resolve()
        external: list[str] = []
        for line in porcelain.splitlines():
            if not line.startswith("??"):
                continue
            rel = line[2:].strip()
            if rel.startswith('"') and rel.endswith('"'):
                rel = rel[1:-1]
            if not rel:
                continue
            # 统一去掉尾部斜杠，便于判断
            rel_norm = rel.rstrip("/")
            # Forge 内部元数据目录，永不视为“外部新建项目文件”
            if rel_norm == ".forge" or rel_norm.startswith(".forge/"):
                continue
            abs_p = str((root / rel_norm).resolve())
            if abs_p in known or rel_norm in known or rel in known:
                continue
            # 只报告普通文件；未跟踪目录本身不构成“外部新建文件”冲突
            if os.path.isfile(abs_p):
                external.append(abs_p)
        return external

    def _invalidate_detect_cache(self) -> None:
        """失效 detect 缓存（非 git 仓库 / World 不可达时禁止复用旧报告）。"""
        self._last_detect_version = None
        self._last_detect_key = None
        self._last_detect_report = None

    @staticmethod
    def _clone_report(r: SyncReport) -> SyncReport:
        """返回报告的浅拷贝，避免调用方改动缓存里的列表字段。"""
        return SyncReport(**r.to_dict())

    def _detect_cache_key(
        self, world_version: int, c_disk: str, current_hashes: dict, untracked: str
    ) -> tuple:
        """detect() 结果的完整输入指纹（缓存键）。

        覆盖影响三态判定的所有输入：
        - `world_version`：唯一需要 World RPC 收据历史的部分；
        - 同步水位（disk_synced_version / last_known_commit / last_known_file_hashes）：
          sync() 期间会变（external_sync 重算 hash、mark_disk_synced 推进水位），
          必须纳入，否则 sync 后的第二次 detect 会误命中旧报告；
        - 磁盘侧（git HEAD、已知文件实时 hash、git untracked 状态）：
          外部编辑/新文件/新 commit 都体现在这里。

        任一输入变化 → 缓存失效 → 全量重算，语义与无缓存完全一致。
        """
        s = self._state
        return (
            world_version,
            s.disk_synced_version,
            s.last_known_commit,
            tuple(sorted(s.last_known_file_hashes.items())),
            c_disk,
            untracked,
            tuple(sorted(current_hashes.items())),
        )

    # ── detection ──────────────────────────────────────────────

    def detect(self) -> SyncReport:
        if not is_git_repo(self.project_root):
            self._invalidate_detect_cache()
            return SyncReport(status=NOT_A_GIT_REPO, detail="workspace is not a git repository")

        try:
            world_version = self._world.get_version()
        except Exception as e:
            self._invalidate_detect_cache()
            return SyncReport(
                status=WORLD_UNAVAILABLE,
                world_version=None,
                disk_commit=git_head_commit(self.project_root),
                known_commit=self._state.last_known_commit,
                disk_synced_version=self._state.disk_synced_version,
                detail=(
                    f"无法访问 World（veritasd）：{e}。"
                    "请先恢复 veritasd 后重试 forge_sync；"
                    "当前不推进任何同步水位。"
                ),
            )

        # P3-4：先取廉价磁盘侧指纹（本地 git + hash + untracked），
        # 若 World version 与指纹均未变化，直接复用上次报告，跳过全量 receipt 扫描。
        c_disk = git_head_commit(self.project_root)
        current_hashes = self._current_hashes()
        try:
            untracked = git_status_porcelain_untracked_all(self.project_root)
        except Exception as e:
            # git status 故障与 World receipt 查询失败同级：无法识别外部 untracked，
            # 不得伪装成 IN_SYNC（保持原语义，git status 失败 → WORLD_UNAVAILABLE）。
            self._invalidate_detect_cache()
            return SyncReport(
                status=WORLD_UNAVAILABLE,
                world_version=None,
                disk_commit=c_disk,
                known_commit=self._state.last_known_commit,
                disk_synced_version=self._state.disk_synced_version,
                detail=(
                    f"无法读取 git 工作区状态：{e}。"
                    "无法识别外部 untracked 文件，禁止把“看不见”伪装成 IN_SYNC。"
                ),
            )
        cache_key = self._detect_cache_key(world_version, c_disk, current_hashes, untracked)
        if self._last_detect_report is not None and self._last_detect_key == cache_key:
            return self._clone_report(self._last_detect_report)

        s = self._state.disk_synced_version
        try:
            world_file_receipts = self._world_file_receipts_beyond(s)
            external_untracked = self._external_untracked_paths()
        except Exception as e:
            self._invalidate_detect_cache()
            return SyncReport(
                status=WORLD_UNAVAILABLE,
                world_version=None,
                disk_commit=git_head_commit(self.project_root),
                known_commit=self._state.last_known_commit,
                disk_synced_version=s,
                detail=(
                    f"无法查询 World receipt 历史：{e}。"
                    "无法构造 Forge 已知路径集合，禁止把“看不见”伪装成 IN_SYNC。"
                ),
            )

        world_advanced = bool(world_file_receipts)
        disk_advanced, divergent = self._disk_advanced()

        c_disk = git_head_commit(self.project_root)
        c_known = self._state.last_known_commit

        if external_untracked:
            return SyncReport(
                status=CONFLICT,
                conflict_kind=CONFLICT_UNTRACKED_EXTERNAL,
                world_version=self._world_version(),
                disk_commit=c_disk,
                known_commit=c_known,
                disk_synced_version=s,
                world_advanced=world_advanced,
                disk_advanced=True,
                divergent_paths=list(external_untracked),
                detail=(
                    "发现 Forge 从未跟踪过的外部新建文件（git untracked）。"
                    "禁止静默视为 IN_SYNC；请确认这些文件是否应纳入项目后再继续。"
                ),
            )

        if world_advanced and disk_advanced:
            report = SyncReport(
                status=CONFLICT,
                conflict_kind=CONFLICT_CONTENT,
                world_version=self._world_version(),
                disk_commit=c_disk,
                known_commit=c_known,
                disk_synced_version=s,
                world_advanced=True,
                disk_advanced=True,
                divergent_paths=divergent,
                detail=(
                    "World 与 Disk/Git 在共同已知状态之后都发生了独立变化，"
                    "禁止自动选择任一侧覆盖。请显式决策。"
                ),
            )
        elif world_advanced:
            report = SyncReport(
                status=FAST_FORWARD_WORLD_TO_DISK,
                world_version=self._world_version(),
                disk_commit=c_disk,
                known_commit=c_known,
                disk_synced_version=s,
                world_advanced=True,
                disk_advanced=False,
                detail=(
                    f"World 有 {len(world_file_receipts)} 笔未同步到磁盘的 forge 变更，"
                    "可沿 World → Disk 方向安全推进。"
                ),
            )
        elif disk_advanced:
            report = SyncReport(
                status=FAST_FORWARD_DISK_TO_WORLD,
                world_version=self._world_version(),
                disk_commit=c_disk,
                known_commit=c_known,
                disk_synced_version=s,
                world_advanced=False,
                disk_advanced=True,
                divergent_paths=divergent,
                detail=(
                    "Disk/Git 相对已知状态有新的外部变化，"
                    "可沿 Disk → World 方向记录外部同步。"
                ),
            )
        else:
            report = SyncReport(
                status=IN_SYNC,
                world_version=self._world_version(),
                disk_commit=c_disk,
                known_commit=c_known,
                disk_synced_version=s,
                detail="World 与 Disk/Git 处于同一已知状态。",
            )

        if report.status == CONFLICT and report.conflict_kind == CONFLICT_CONTENT:
            report.diff_hint = self._build_diff_hint(divergent)

        self._last_detect_version = world_version
        self._last_detect_key = cache_key
        self._last_detect_report = report
        return report

    def _build_diff_hint(self, divergent_paths: list[str]) -> str:
        try:
            return git_diff(self.project_root, divergent_paths or None)
        except Exception as e:
            print(f"[sync] _build_diff_hint failed: {e}", file=sys.stderr)
            return ""

    # ── resolution ─────────────────────────────────────────────

    def sync(self) -> SyncReport:
        """执行显式同步（契约 §7 forge sync）。"""
        report = self.detect()

        if report.status == IN_SYNC:
            return report

        if report.status == NOT_A_GIT_REPO:
            return report

        if report.status == WORLD_UNAVAILABLE:
            return report

        if report.status == CONFLICT:
            return report

        if report.status == FAST_FORWARD_DISK_TO_WORLD:
            self._state.record_external_sync()
            return self.detect()

        if report.status == FAST_FORWARD_WORLD_TO_DISK:
            return self._forward_world_to_disk()

        return report

    def _forward_world_to_disk(self) -> SyncReport:
        """World → Disk 安全推进：逐笔物化已确认磁盘未分叉的 forge receipt。

        规则 A：任何一笔失败 / 分叉都不得推进 disk_synced_version；
        一旦失败即转为 CONFLICT 并停止。
        """
        if self._file_projection is None:
            return SyncReport(
                status=CONFLICT,
                conflict_kind=CONFLICT_CONTENT,
                detail="no FileProjection available to fast-forward World → Disk",
            )

        s = self._state.disk_synced_version
        receipts = self._world_file_receipts_beyond(s)
        for receipt in receipts:
            disk_advanced, divergent = self._disk_advanced()
            if disk_advanced:
                return SyncReport(
                    status=CONFLICT,
                    conflict_kind=CONFLICT_CONTENT,
                    world_version=self._world_version(),
                    disk_commit=git_head_commit(self.project_root),
                    known_commit=self._state.last_known_commit,
                    disk_synced_version=s,
                    divergent_paths=divergent,
                    detail="fast-forward 中止：磁盘在同步期间发生了外部变化。",
                )
            try:
                conflicts = self._receipt_conflicts_with_disk(receipt)
            except Exception as e:
                return SyncReport(
                    status=CONFLICT,
                    conflict_kind=CONFLICT_CONTENT,
                    world_version=self._world_version(),
                    disk_commit=git_head_commit(self.project_root),
                    known_commit=self._state.last_known_commit,
                    disk_synced_version=s,
                    divergent_paths=[],
                    detail=(
                        f"fast-forward 中止于 version={getattr(receipt, 'version', '?')}："
                        f"无法判定磁盘是否与 receipt 冲突：{e}"
                    ),
                )
            if conflicts:
                return SyncReport(
                    status=CONFLICT,
                    conflict_kind=CONFLICT_CONTENT,
                    world_version=self._world_version(),
                    disk_commit=git_head_commit(self.project_root),
                    known_commit=self._state.last_known_commit,
                    disk_synced_version=s,
                    divergent_paths=conflicts,
                    detail=(
                        f"fast-forward 中止于 version={getattr(receipt, 'version', '?')}："
                        "磁盘存在 World 未记录的手动修改，禁止覆盖。"
                    ),
                )
            try:
                result = self._file_projection.apply(receipt, getattr(receipt, "delta", None))
            except Exception as e:
                result = None
                err = str(e)
            if result is None or not getattr(result, "success", False):
                reason = getattr(result, "reason", "") if result else err
                uncertain = list(getattr(result, "uncertain_paths", None) or [])
                return SyncReport(
                    status=CONFLICT,
                    conflict_kind=CONFLICT_CONTENT,
                    world_version=self._world_version(),
                    disk_commit=git_head_commit(self.project_root),
                    known_commit=self._state.last_known_commit,
                    disk_synced_version=s,
                    divergent_paths=uncertain,
                    detail=(
                        f"fast-forward 失败于 version={getattr(receipt, 'version', '?')}: {reason}"
                        + (
                            "\n以下文件处于未知状态，需要手动检查："
                            + ", ".join(uncertain)
                            if uncertain
                            else ""
                        )
                    ),
                )

        return self.detect()

    def _receipt_conflicts_with_disk(self, receipt) -> list[str]:
        """返回该 receipt 要修改/删除、但磁盘相对已知基线已分叉的路径列表。

        已知基线 = sync_state.last_known_file_hashes。未在基线中的既有文件
        视为"World 未记录的手动修改"，禁止被 fast-forward 覆盖。

        prepare() 失败（例如已有文件读不出来）时向上抛：无法判定"无冲突"，
        不得把失败当成空冲突列表从而允许 fast-forward 覆盖。
        """
        if self._file_projection is None:
            return []
        info = self._file_projection.prepare(getattr(receipt, "delta", None))
        if not info:
            return []
        touched = list(info.get("files_modified", []) or []) + list(
            info.get("files_deleted", []) or []
        )
        known = self._state.last_known_file_hashes
        conflicts: list[str] = []
        for p in touched:
            if p not in known:
                conflicts.append(p)
            elif hash_file(p) != known[p]:
                conflicts.append(p)
        return conflicts

    # ── runtime external-change guard ──────────────────────────

    def apply_disk_to_world_decision(self, decision, report) -> "SyncReport":
        """Phase B：在已授权 DECIDED(disk_to_world)+generation 下执行 workspace-wide supersede。

        控制面负责 classify / clear / supersede；本方法只：
        preflight（current observation == G）→ accept_disk_wins → detect verify。
        任何 SyncState 写入仅发生在 preflight 通过之后。
        不 clear SyncDecision，不打开 PENDING。
        """
        from forge.sync.decision import (
            APPLICABLE,
            DIRECTION_DISK_TO_WORLD,
            STATUS_DECIDED,
            classify_decision_applicability,
        )

        # Preflight：在任何 SyncState mutation 之前
        if decision is None or getattr(decision, "status", None) != STATUS_DECIDED:
            return SyncReport(
                status=getattr(report, "status", CONFLICT) or CONFLICT,
                detail="phase_b:preflight_rejected: decision not DECIDED",
            )
        if getattr(decision, "direction", None) != DIRECTION_DISK_TO_WORLD:
            return SyncReport(
                status=getattr(report, "status", CONFLICT) or CONFLICT,
                detail="phase_b:preflight_rejected: direction is not disk_to_world",
            )
        gen = getattr(decision, "generation", None)
        if not isinstance(gen, dict):
            return SyncReport(
                status=getattr(report, "status", CONFLICT) or CONFLICT,
                detail="phase_b:preflight_stale: missing generation",
            )

        kind = classify_decision_applicability(decision, report, self._state)
        if kind != APPLICABLE:
            return SyncReport(
                status=getattr(report, "status", CONFLICT) or CONFLICT,
                conflict_kind=getattr(report, "conflict_kind", None),
                world_version=getattr(report, "world_version", None),
                disk_commit=getattr(report, "disk_commit", "") or "",
                known_commit=getattr(report, "known_commit", "") or "",
                disk_synced_version=getattr(report, "disk_synced_version", 0) or 0,
                divergent_paths=list(getattr(report, "divergent_paths", None) or []),
                detail=f"phase_b:preflight_stale:{kind}",
            )

        # 严格：current.world_version 必须等于 G.world_version（classify 已查；双保险）
        try:
            authorized = int(gen.get("world_version"))
        except (TypeError, ValueError):
            return SyncReport(
                status=CONFLICT,
                detail="phase_b:preflight_stale: invalid generation.world_version",
            )
        cur_wv = getattr(report, "world_version", None)
        try:
            cur_wv_i = int(cur_wv) if cur_wv is not None else None
        except (TypeError, ValueError):
            cur_wv_i = None
        if cur_wv_i != authorized:
            return SyncReport(
                status=getattr(report, "status", CONFLICT) or CONFLICT,
                world_version=cur_wv,
                detail=(
                    "phase_b:preflight_stale: world_version mismatch "
                    f"current={cur_wv_i!r} generation={authorized!r}"
                ),
            )

        # 唯一 SyncState mutation
        self._state.accept_disk_wins(
            authorized_world_version=authorized,
            source="user_reconcile_disk_wins",
            recompute_hashes=True,
        )
        # 不变量：watermark_after <= G.world_version
        if self._state.disk_synced_version > authorized:
            return SyncReport(
                status=CONFLICT,
                detail=(
                    "phase_b:invariant_violation: watermark "
                    f"{self._state.disk_synced_version} > authorized {authorized}"
                ),
            )

        return self.detect()

    def apply_world_to_disk_decision(self, decision, report) -> "SyncReport":
        """Phase C：DECIDED(world_to_disk)+授权 generation 下 per-receipt 物化。

        - 执行前冻结 authorized receipt sequence；发现 version > G.world_version → 授权异常停止
        - 每笔：FileProjection.apply 成功 → mark_disk_synced(version)（watermark 唯一推进）
        - 不 clear SyncDecision；progress 写回 decision.mark_count / last_marked_version
        - mark_count==0 时要求 classify==applicable；mark_count>0 时不因 fingerprint/dsv 偏离而 supersede
        """
        from forge.sync.decision import (
            APPLICABLE,
            DIRECTION_WORLD_TO_DISK,
            PARTIAL_EXECUTION,
            STATUS_DECIDED,
            classify_decision_applicability,
        )

        if decision is None or getattr(decision, "status", None) != STATUS_DECIDED:
            return SyncReport(
                status=getattr(report, "status", CONFLICT) or CONFLICT,
                detail="phase_c:preflight_rejected: decision not DECIDED",
            )
        if getattr(decision, "direction", None) != DIRECTION_WORLD_TO_DISK:
            return SyncReport(
                status=getattr(report, "status", CONFLICT) or CONFLICT,
                detail="phase_c:preflight_rejected: direction is not world_to_disk",
            )
        gen = getattr(decision, "generation", None)
        if not isinstance(gen, dict):
            return SyncReport(
                status=CONFLICT,
                detail="phase_c:preflight_stale: missing generation",
            )
        try:
            authorized_wv = int(gen.get("world_version"))
        except (TypeError, ValueError):
            return SyncReport(
                status=CONFLICT,
                detail="phase_c:preflight_stale: invalid generation.world_version",
            )

        mark_count = int(getattr(decision, "mark_count", 0) or 0)
        if mark_count == 0:
            kind = classify_decision_applicability(decision, report, self._state)
            if kind != APPLICABLE:
                return SyncReport(
                    status=getattr(report, "status", CONFLICT) or CONFLICT,
                    conflict_kind=getattr(report, "conflict_kind", None),
                    world_version=getattr(report, "world_version", None),
                    disk_commit=getattr(report, "disk_commit", "") or "",
                    known_commit=getattr(report, "known_commit", "") or "",
                    disk_synced_version=self._state.disk_synced_version,
                    divergent_paths=list(getattr(report, "divergent_paths", None) or []),
                    detail=f"phase_c:preflight_stale:{kind}",
                )
        else:
            # partial：world_version 必须仍等于 G；其它偏离由 classify→PARTIAL_EXECUTION 处理
            cur_wv = getattr(report, "world_version", None)
            try:
                cur_wv_i = int(cur_wv) if cur_wv is not None else None
            except (TypeError, ValueError):
                cur_wv_i = None
            if cur_wv_i != authorized_wv:
                return SyncReport(
                    status=getattr(report, "status", CONFLICT) or CONFLICT,
                    world_version=cur_wv,
                    disk_synced_version=self._state.disk_synced_version,
                    detail=(
                        "phase_c:execution_failed:world_version mismatch "
                        f"current={cur_wv_i!r} generation={authorized_wv!r}"
                    ),
                )

        if self._file_projection is None:
            return SyncReport(
                status=CONFLICT,
                conflict_kind=CONFLICT_CONTENT,
                detail="phase_c: no FileProjection available for world_to_disk",
            )

        # 冻结 execution set（只查询一次）
        s = self._state.disk_synced_version
        try:
            pending = self._world_file_receipts_beyond(s)
        except Exception as e:
            return SyncReport(
                status=WORLD_UNAVAILABLE,
                detail=f"phase_c: cannot list receipts: {e}",
            )

        from forge.sync.attempt import ReconcileAttemptStore

        attempt_store = ReconcileAttemptStore(
            Path(self.project_root) / ".forge"
        )
        existing_attempt = attempt_store.load()
        if existing_attempt is not None and existing_attempt.status == "IN_PROGRESS":
            return SyncReport(
                status=CONFLICT,
                detail=(
                    "phase_d: in_progress ReconcileAttempt exists; "
                    "recovery must run before apply_world_to_disk_decision"
                ),
            )

        # 授权异常：任何 version > G.world_version 必须 stop（不是静默过滤）
        for r in pending:
            ver = int(getattr(r, "version", 0) or 0)
            if ver > authorized_wv:
                return SyncReport(
                    status=CONFLICT,
                    world_version=getattr(report, "world_version", None),
                    disk_synced_version=self._state.disk_synced_version,
                    detail=(
                        "phase_c:authorization_error: receipt version "
                        f"{ver} > generation.world_version {authorized_wv}"
                    ),
                )

        sequence = [
            r
            for r in pending
            if int(getattr(r, "version", 0) or 0) <= authorized_wv
        ]
        # sequence 已冻结；循环中不得重新 get_receipts 纳入新 receipt

        attempt = attempt_store.create(decision, sequence)

        for i, receipt in enumerate(sequence):
            ver = int(getattr(receipt, "version", 0) or 0)

            # Phase D：apply 前 durable 写 expected effects
            try:
                info = self._file_projection.prepare(
                    getattr(receipt, "delta", None)
                ) or {}
            except Exception:
                info = {}
            written_before = list(info.get("files_modified", []) or [])
            deleted_before = list(info.get("files_deleted", []) or [])
            expected_effect = {
                "written_paths": written_before,
                "deleted_paths": deleted_before,
            }
            attempt_store.set_expected_effect(
                attempt, i, effect={"paths": expected_effect}
            )

            try:
                result = self._file_projection.apply(
                    receipt, getattr(receipt, "delta", None)
                )
            except Exception as e:
                result = None
                err = str(e)
            else:
                err = ""
            if result is None or not getattr(result, "success", False):
                reason = getattr(result, "reason", "") if result else err
                return SyncReport(
                    status=CONFLICT,
                    conflict_kind=CONFLICT_CONTENT,
                    world_version=getattr(report, "world_version", None),
                    disk_commit=git_head_commit(self.project_root),
                    known_commit=self._state.last_known_commit,
                    disk_synced_version=self._state.disk_synced_version,
                    detail=(
                        f"phase_c:execution_failed: apply failed at version={ver}: {reason}"
                    ),
                )

            written = list(getattr(result, "written_paths", None) or [])
            deleted = list(getattr(result, "deleted_paths", None) or [])
            # 若 FileProjection 用不同字段名，尽量兼容
            if not written and not deleted:
                info = {}
                try:
                    info = self._file_projection.prepare(
                        getattr(receipt, "delta", None)
                    ) or {}
                except Exception:
                    info = {}
                written = list(info.get("files_modified", []) or [])
                deleted = list(info.get("files_deleted", []) or [])

            self._state.mark_disk_synced(
                ver,
                written,
                deleted,
                source="user_reconcile_world_wins",
            )
            if self._state.disk_synced_version > authorized_wv:
                return SyncReport(
                    status=CONFLICT,
                    detail=(
                        "phase_c:invariant_violation: watermark "
                        f"{self._state.disk_synced_version} > authorized {authorized_wv}"
                    ),
                )
            decision.mark_count = int(getattr(decision, "mark_count", 0) or 0) + 1
            decision.last_marked_version = ver

        return self.detect()

    def world_available(self) -> bool:


        """World（veritasd）是否可访问。"""
        try:
            self._world.get_version()
            return True
        except Exception as e:
            print(f"[sync] world_available failed: {e}", file=sys.stderr)
            return False

    def disk_change_detected(self) -> bool:
        """纯磁盘侧：磁盘/Git 相对已知状态是否发生了外部变化。

        与 World 是否可达无关。P2-1 direct_disk 路径需要在 World 不可达时
        仍然执行这一半检查——不能因为绕过 Veritas 就把外部变更 guard 一起绕过。
        """
        if not is_git_repo(self.project_root):
            return False
        disk_advanced, _ = self._disk_advanced()
        return disk_advanced

    def external_change_detected(self) -> bool:
        """运行期间：World 不可达，或磁盘/Git 相对已知状态发生了变化。

        World 不可达时同样视为“需要停止写入”——不是磁盘冲突，
        而是此时任何 Forge 写盘都不会被 World 记录，会产生无痕分叉。
        """
        if not self.world_available():
            return True
        return self.disk_change_detected()

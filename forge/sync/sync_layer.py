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

性能备注（已知后续优化项，非本轮范围）：
detect() 当前每次全量查询 receipt 历史（get_receipts_since(0)）构造
forge_known_paths 集合，用于识别外部 untracked。若 receipt 量级增长导致
detect() 变慢，应增加进程内 path 缓存或增量索引，不属于本轮修复范围。
"""

from __future__ import annotations

import os
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
        except Exception:
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

    @property
    def state(self) -> SyncState:
        return self._state

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

    # ── detection ──────────────────────────────────────────────

    def detect(self) -> SyncReport:
        if not is_git_repo(self.project_root):
            return SyncReport(status=NOT_A_GIT_REPO, detail="workspace is not a git repository")

        try:
            self._world.get_version()
        except Exception as e:
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

        s = self._state.disk_synced_version
        try:
            world_file_receipts = self._world_file_receipts_beyond(s)
            external_untracked = self._external_untracked_paths()
        except Exception as e:
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
        return report

    def _build_diff_hint(self, divergent_paths: list[str]) -> str:
        try:
            return git_diff(self.project_root, divergent_paths or None)
        except Exception:
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

    def world_available(self) -> bool:
        """World（veritasd）是否可访问。"""
        try:
            self._world.get_version()
            return True
        except Exception:
            return False

    def external_change_detected(self) -> bool:
        """运行期间：World 不可达，或磁盘/Git 相对已知状态发生了变化。

        World 不可达时同样视为“需要停止写入”——不是磁盘冲突，
        而是此时任何 Forge 写盘都不会被 World 记录，会产生无痕分叉。
        """
        if not self.world_available():
            return True
        if not is_git_repo(self.project_root):
            return False
        disk_advanced, _ = self._disk_advanced()
        return disk_advanced

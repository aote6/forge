"""SyncLayer — World ↔ Disk/Git 三态同步判定与安全推进。

契约 §3：判定必须同时考虑
- Git commit ancestry（HEAD 是否变化）
- working tree / 文件 hash
- World sync metadata（disk_synced_version + receipts）

产出四态：
- IN_SYNC
- FAST_FORWARD_DISK_TO_WORLD
- FAST_FORWARD_WORLD_TO_DISK
- CONFLICT
（非 Git 仓库 → NOT_A_GIT_REPO，契约 §决策 5：不做无 Git 的第二套状态机）

契约 §4：任何未解决分叉 MUST STOP；禁止 skip → success → advance。
契约 §7：`forge sync` = 检测 → 依状态安全推进 / 报告冲突 → 更新元数据 → 记录同步事实。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from forge.sync.git_utils import (
    git_diff,
    git_head_commit,
    git_status_porcelain,
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
        }

    def format(self) -> str:
        lines = [f"sync_status: {self.status}"]
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
        """source=forge_tool 且涉及文件的 receipt（version > `version`），升序。"""
        try:
            receipts = self._world.get_receipts_since(version)
        except Exception:
            return []
        out = [
            r
            for r in receipts
            if getattr(r, "source", "forge_tool") == "forge_tool"
            and _touches_files(getattr(r, "delta", None))
        ]
        out.sort(key=lambda r: getattr(r, "version", 0))
        return out

    def _world_version(self) -> Optional[int]:
        try:
            return self._world.get_version()
        except Exception:
            return None

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

    # ── detection ──────────────────────────────────────────────

    def detect(self) -> SyncReport:
        if not is_git_repo(self.project_root):
            return SyncReport(status=NOT_A_GIT_REPO, detail="workspace is not a git repository")

        s = self._state.disk_synced_version
        world_file_receipts = self._world_file_receipts_beyond(s)
        world_advanced = bool(world_file_receipts)
        disk_advanced, divergent = self._disk_advanced()

        c_disk = git_head_commit(self.project_root)
        c_known = self._state.last_known_commit

        if world_advanced and disk_advanced:
            report = SyncReport(
                status=CONFLICT,
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

        if report.status == CONFLICT:
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

        if report.status == CONFLICT:
            # MUST STOP；不覆盖磁盘 / 不覆盖 World / 不推进水位。
            return report

        if report.status == FAST_FORWARD_DISK_TO_WORLD:
            # 记录外部同步事实，不伪造 World transaction。
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
                detail="no FileProjection available to fast-forward World → Disk",
            )

        s = self._state.disk_synced_version
        receipts = self._world_file_receipts_beyond(s)
        for receipt in receipts:
            # 双重保险：物化前再次确认磁盘未相对已知状态漂移。
            disk_advanced, divergent = self._disk_advanced()
            if disk_advanced:
                return SyncReport(
                    status=CONFLICT,
                    world_version=self._world_version(),
                    disk_commit=git_head_commit(self.project_root),
                    known_commit=self._state.last_known_commit,
                    disk_synced_version=s,
                    divergent_paths=divergent,
                    detail="fast-forward 中止：磁盘在同步期间发生了外部变化。",
                )
            # 禁止隐式覆盖：要改/删的既有文件必须是已知基线（已同步且 hash 一致），
            # 否则说明磁盘存在 World 未记录的手动修改（契约 §4）。
            conflicts = self._receipt_conflicts_with_disk(receipt)
            if conflicts:
                return SyncReport(
                    status=CONFLICT,
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
                return SyncReport(
                    status=CONFLICT,
                    world_version=self._world_version(),
                    disk_commit=git_head_commit(self.project_root),
                    known_commit=self._state.last_known_commit,
                    disk_synced_version=s,
                    detail=f"fast-forward 失败于 version={getattr(receipt, 'version', '?')}: {reason}",
                )

        return self.detect()

    def _receipt_conflicts_with_disk(self, receipt) -> list[str]:
        """返回该 receipt 要修改/删除、但磁盘相对已知基线已分叉的路径列表。

        已知基线 = sync_state.last_known_file_hashes。未在基线中的既有文件
        视为"World 未记录的手动修改"，禁止被 fast-forward 覆盖。
        """
        if self._file_projection is None:
            return []
        try:
            info = self._file_projection.prepare(getattr(receipt, "delta", None))
        except Exception:
            return []
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

    def external_change_detected(self) -> bool:
        """运行期间：磁盘/Git 是否相对已知状态发生了变化。

        用于 Runtime 在持锁写入前检测外部修改；一旦变化即应停止写入并重新对账
        （契约 §7：发现外部磁盘变化立即停止当前写操作，进入重新对账）。
        """
        if not is_git_repo(self.project_root):
            return False
        disk_advanced, _ = self._disk_advanced()
        return disk_advanced

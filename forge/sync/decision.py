"""SyncDecision — 用户/主 AI 对一次同步策略点的显式决议。

落点：`<project_root>/.forge/sync_decision.json`

契约：docs/RUNTIME_STATE_CONTRACT.md §3
协议：docs/standards/sync_decision_reconciliation.md（v1.1 + Phase A）

与 Mutation Confirmation（PendingAction / Execution Pause）严格分离：
  - SyncDecision 粒度是同步策略点，触发于 detect()=CONFLICT|FAST_FORWARD
  - Mutation Confirmation 粒度是单次写工具，不持久化

R2 最小闭环：数据对象 + 持久化；由 Runtime.sync_status 打开 pending，
resolve_sync_decision 关闭；Gate 在 pending 期间拒写与 forge_sync 推进。

Phase A：generation 冻结授权边界；classify_decision_applicability 判定
DECIDED 是否仍 applicable；不执行 reconciliation。
"""
from __future__ import annotations

import hashlib
import json
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

# direction 枚举（契约 §3.2）
DIRECTION_DISK_TO_WORLD = "disk_to_world"
DIRECTION_WORLD_TO_DISK = "world_to_disk"
DIRECTION_ABORT = "abort"
VALID_DIRECTIONS = frozenset(
    {DIRECTION_DISK_TO_WORLD, DIRECTION_WORLD_TO_DISK, DIRECTION_ABORT}
)

# status 枚举
STATUS_PENDING = "pending"
STATUS_DECIDED = "decided"
STATUS_ABORTED = "aborted"
VALID_STATUSES = frozenset({STATUS_PENDING, STATUS_DECIDED, STATUS_ABORTED})

# basis 与 SyncReport.status 对齐（触发决策的状态）
from forge.sync.sync_layer import (
    CONFLICT,
    FAST_FORWARD_DISK_TO_WORLD,
    FAST_FORWARD_WORLD_TO_DISK,
    IN_SYNC,
)

DECISION_REQUIRED_STATUSES = frozenset(
    {
        CONFLICT,
        FAST_FORWARD_DISK_TO_WORLD,
        FAST_FORWARD_WORLD_TO_DISK,
    }
)

# generation 必需键（Phase A / v1.1 A.2）
GENERATION_REQUIRED_KEYS = frozenset(
    {
        "basis",
        "conflict_kind",
        "world_version",
        "disk_synced_version",
        "known_commit",
        "disk_commit",
        "divergent_paths",
        "path_hash_fingerprint",
    }
)

# classify_decision_applicability 返回值
APPLICABLE = "applicable"
STALE = "stale"
ALREADY_IN_SYNC = "already_in_sync"
NOT_DECIDED = "not_decided"
LEGACY_NO_GENERATION = "legacy_no_generation"
# Phase C：已有 per-receipt mark 的 DECIDED，不得走普通 supersede
PARTIAL_EXECUTION = "partial_execution"


def fingerprint_managed_disk(paths: Iterable[str]) -> str:
    """对当前 path set 做稳定内容指纹（sha256）。

    每个 path 使用 hash_file(path)；缺失文件记为 MISSING。
    输入必须是**当前**路径集合，不得只使用 generation 内旧 path 列表。
    """
    from forge.sync.git_utils import hash_file

    items: list[str] = []
    for path in sorted({str(p) for p in paths if p}):
        digest = hash_file(path)
        items.append(f"{path}\0{digest if digest is not None else 'MISSING'}")
    blob = "\n".join(items).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def managed_path_set_for_observation(report: Any, sync_state: Any) -> list[str]:
    """当前观察下的 managed path 集合：divergent_paths ∪ last_known_file_hashes keys。"""
    divergent: list[str] = []
    raw = getattr(report, "divergent_paths", None) or []
    if isinstance(raw, (list, tuple)):
        divergent = [str(p) for p in raw if p]
    known_keys: list[str] = []
    if sync_state is not None:
        try:
            hashes = getattr(sync_state, "last_known_file_hashes", None) or {}
            if isinstance(hashes, dict):
                known_keys = [str(p) for p in hashes.keys() if p]
        except Exception:
            known_keys = []
    return sorted(set(divergent) | set(known_keys))


def build_sync_decision_generation(report: Any, sync_state: Any) -> dict[str, Any]:
    """从 SyncReport + SyncState 冻结 generation（授权边界观察）。

    可在 detect() 之后主动扫 hash（detect 超集）；不要求与 _disk_advanced 短路一致。
    """
    status = str(getattr(report, "status", None) or "")
    conflict_kind = getattr(report, "conflict_kind", None)
    if conflict_kind is not None:
        conflict_kind = str(conflict_kind) or None

    world_version = getattr(report, "world_version", None)
    if world_version is not None:
        try:
            world_version = int(world_version)
        except (TypeError, ValueError):
            world_version = None

    disk_synced_version = getattr(report, "disk_synced_version", None)
    if disk_synced_version is None and sync_state is not None:
        disk_synced_version = getattr(sync_state, "disk_synced_version", 0)
    try:
        disk_synced_version = int(disk_synced_version or 0)
    except (TypeError, ValueError):
        disk_synced_version = 0

    known_commit = str(getattr(report, "known_commit", None) or "")
    disk_commit = str(getattr(report, "disk_commit", None) or "")

    divergent_raw = getattr(report, "divergent_paths", None) or []
    if isinstance(divergent_raw, (list, tuple)):
        divergent_paths = sorted({str(p) for p in divergent_raw if p})
    else:
        divergent_paths = []

    paths = managed_path_set_for_observation(report, sync_state)
    path_hash_fingerprint = fingerprint_managed_disk(paths)

    return {
        "basis": status,
        "conflict_kind": conflict_kind,
        "world_version": world_version,
        "disk_synced_version": disk_synced_version,
        "known_commit": known_commit,
        "disk_commit": disk_commit,
        "divergent_paths": divergent_paths,
        "path_hash_fingerprint": path_hash_fingerprint,
    }


def generation_is_complete(generation: Any) -> bool:
    if not isinstance(generation, dict):
        return False
    for key in GENERATION_REQUIRED_KEYS:
        if key not in generation:
            return False
    fp = generation.get("path_hash_fingerprint")
    if not isinstance(fp, str) or not fp.strip():
        return False
    if not isinstance(generation.get("divergent_paths"), list):
        return False
    return True


def _stale_or_partial(decision: "SyncDecision") -> str:
    """mark_count>0 的 world_to_disk 部分执行态：不得普通 STALE/supersede。"""
    if (
        getattr(decision, "direction", None) == DIRECTION_WORLD_TO_DISK
        and int(getattr(decision, "mark_count", 0) or 0) > 0
    ):
        return PARTIAL_EXECUTION
    return STALE


def classify_decision_applicability(
    decision: SyncDecision | None,
    report: Any,
    sync_state: Any,
) -> str:
    """判定 DECIDED 是否仍可对当前观察授权执行。

    不调用 detect()；由调用方传入最新 report。
    返回：applicable | stale | already_in_sync | not_decided | legacy_no_generation
         | partial_execution

    Phase C：一旦 mark_count>0，观察偏离不得变为可 supersede 的 STALE，
    而返回 partial_execution，由控制面 execution_failed 保留原 DECIDED。
    """
    status = str(getattr(report, "status", None) or "")
    if status == IN_SYNC:
        return ALREADY_IN_SYNC

    if decision is None:
        return NOT_DECIDED

    if decision.status != STATUS_DECIDED:
        return NOT_DECIDED

    direction = decision.direction
    if direction not in (DIRECTION_DISK_TO_WORLD, DIRECTION_WORLD_TO_DISK):
        return NOT_DECIDED

    gen = decision.generation
    if not generation_is_complete(gen):
        if int(getattr(decision, "mark_count", 0) or 0) > 0:
            return PARTIAL_EXECUTION
        return LEGACY_NO_GENERATION

    def _as_int(v: Any) -> int | None:
        if v is None:
            return None
        try:
            return int(v)
        except (TypeError, ValueError):
            return None

    if str(gen.get("basis") or "") != status:
        return _stale_or_partial(decision)

    report_ck = getattr(report, "conflict_kind", None)
    if report_ck is not None:
        report_ck = str(report_ck) or None
    gen_ck = gen.get("conflict_kind")
    if gen_ck is not None:
        gen_ck = str(gen_ck) or None
    if gen_ck != report_ck:
        return _stale_or_partial(decision)

    if _as_int(gen.get("world_version")) != _as_int(
        getattr(report, "world_version", None)
    ):
        return _stale_or_partial(decision)

    report_dsv = getattr(report, "disk_synced_version", None)
    if report_dsv is None and sync_state is not None:
        report_dsv = getattr(sync_state, "disk_synced_version", None)
    if _as_int(gen.get("disk_synced_version")) != _as_int(report_dsv):
        return _stale_or_partial(decision)

    if str(gen.get("known_commit") or "") != str(
        getattr(report, "known_commit", None) or ""
    ):
        return _stale_or_partial(decision)

    if str(gen.get("disk_commit") or "") != str(
        getattr(report, "disk_commit", None) or ""
    ):
        return _stale_or_partial(decision)

    report_div = getattr(report, "divergent_paths", None) or []
    if isinstance(report_div, (list, tuple)):
        report_div_sorted = sorted({str(p) for p in report_div if p})
    else:
        report_div_sorted = []
    gen_div = gen.get("divergent_paths") or []
    if not isinstance(gen_div, list):
        return _stale_or_partial(decision)
    gen_div_sorted = sorted({str(p) for p in gen_div if p})
    if gen_div_sorted != report_div_sorted:
        return _stale_or_partial(decision)

    current_paths = managed_path_set_for_observation(report, sync_state)
    current_fp = fingerprint_managed_disk(current_paths)
    if current_fp != str(gen.get("path_hash_fingerprint") or ""):
        return _stale_or_partial(decision)

    return APPLICABLE



@dataclass
class SyncDecision:
    """一次同步策略点的决议（可持久化）。"""

    decision_id: str
    basis: str
    direction: str | None = None
    status: str = STATUS_PENDING
    created_at: float = 0.0
    decided_at: float | None = None
    generation: dict[str, Any] | None = None
    # Phase C progress（非完整 crash recovery；仅防止 partial 后被 supersede）
    mark_count: int = 0
    last_marked_version: int | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "decision_id": self.decision_id,
            "basis": self.basis,
            "direction": self.direction,
            "status": self.status,
            "created_at": self.created_at,
            "decided_at": self.decided_at,
            "mark_count": int(self.mark_count or 0),
            "last_marked_version": self.last_marked_version,
        }
        if self.generation is not None:
            d["generation"] = dict(self.generation)
        return d

    @classmethod
    def from_dict(cls, data: Any) -> SyncDecision | None:
        if not isinstance(data, dict):
            return None
        did = str(data.get("decision_id") or "").strip()
        basis = str(data.get("basis") or "").strip()
        if not did or not basis:
            return None
        status = str(data.get("status") or STATUS_PENDING).strip()
        if status not in VALID_STATUSES:
            status = STATUS_PENDING
        direction = data.get("direction")
        if direction is not None:
            direction = str(direction).strip() or None
            if direction not in VALID_DIRECTIONS:
                direction = None
        try:
            created_at = float(data.get("created_at") or 0.0)
        except (TypeError, ValueError):
            created_at = 0.0
        decided_at = data.get("decided_at")
        if decided_at is not None:
            try:
                decided_at = float(decided_at)
            except (TypeError, ValueError):
                decided_at = None
        generation = data.get("generation")
        if generation is not None and not isinstance(generation, dict):
            generation = None
        try:
            mark_count = int(data.get("mark_count") or 0)
        except (TypeError, ValueError):
            mark_count = 0
        if mark_count < 0:
            mark_count = 0
        last_marked_version = data.get("last_marked_version")
        if last_marked_version is not None:
            try:
                last_marked_version = int(last_marked_version)
            except (TypeError, ValueError):
                last_marked_version = None
        return cls(
            decision_id=did,
            basis=basis,
            direction=direction,
            status=status,
            created_at=created_at,
            decided_at=decided_at,
            generation=generation,
            mark_count=mark_count,
            last_marked_version=last_marked_version,
        )

    @classmethod
    def new_pending(
        cls, basis: str, generation: dict[str, Any] | None = None
    ) -> SyncDecision:
        return cls(
            decision_id=f"sd_{uuid.uuid4().hex[:12]}",
            basis=str(basis),
            direction=None,
            status=STATUS_PENDING,
            created_at=time.time(),
            decided_at=None,
            generation=generation,
        )

    def apply_direction(self, direction: str) -> None:
        """只改 direction/status/时间；不得重算或清除 generation。"""
        direction = str(direction or "").strip()
        if direction not in VALID_DIRECTIONS:
            raise ValueError(
                f"direction must be one of {sorted(VALID_DIRECTIONS)}, got {direction!r}"
            )
        self.direction = direction
        self.decided_at = time.time()
        if direction == DIRECTION_ABORT:
            self.status = STATUS_ABORTED
        else:
            self.status = STATUS_DECIDED


class SyncDecisionStore:
    """`.forge/sync_decision.json` 加载/保存。"""

    def __init__(self, project_root: str | Path):
        self.project_root = str(Path(project_root).expanduser().resolve())
        self._dir = Path(self.project_root) / ".forge"
        self._file = self._dir / "sync_decision.json"

    @property
    def path(self) -> Path:
        return self._file

    def load(self) -> SyncDecision | None:
        if not self._file.exists():
            return None
        try:
            raw = self._file.read_text(encoding="utf-8").strip()
            if not raw:
                return None
            data = json.loads(raw)
            return SyncDecision.from_dict(data)
        except Exception as e:
            print(f"[sync_decision] load failed: {e}", file=sys.stderr)
            return None

    def save(self, decision: SyncDecision) -> None:
        """原子替换（tmp + replace）。stale supersede 必须用本方法一次写入新 PENDING。"""
        self._dir.mkdir(parents=True, exist_ok=True)
        tmp = self._file.with_suffix(".tmp")
        text = json.dumps(decision.to_dict(), ensure_ascii=False, indent=2) + "\n"
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(self._file)

    def clear(self) -> None:
        """Remove the durable decision file (after resolved / abort)."""
        try:
            if self._file.exists():
                self._file.unlink()
        except Exception as e:
            print(f"[sync_decision] clear failed: {e}", file=sys.stderr)


def needs_sync_decision(report_status: str) -> bool:
    return str(report_status or "") in DECISION_REQUIRED_STATUSES


def supersede_decided_with_pending(
    project_root: str | Path,
    report: Any,
    sync_state: Any,
) -> SyncDecision | None:
    """原子用新 PENDING（含新 generation）覆盖旧 DECIDED，并打开 RuntimeState.pending。

    禁止先 clear 再 save。
    若 RuntimeState 已有 HUMAN_INTERVENTION pending：不得创建/覆盖 SyncDecision，
    检查必须在 store.save 之前；返回 None。
    """
    from forge.runtime_state import (
        PHASE_AWAITING_USER,
        PENDING_KIND_HUMAN_INTERVENTION,
        PENDING_KIND_SYNC_DECISION,
        Pending,
        RuntimeStateStore,
    )

    rs_store = RuntimeStateStore(project_root)
    rs = rs_store.load()
    if rs.pending is not None and rs.pending.kind == PENDING_KIND_HUMAN_INTERVENTION:
        return None

    gen = build_sync_decision_generation(report, sync_state)
    basis = str(getattr(report, "status", None) or CONFLICT)
    new_decision = SyncDecision.new_pending(basis=basis, generation=gen)
    store = SyncDecisionStore(project_root)
    store.save(new_decision)

    detail = str(getattr(report, "detail", "") or "")
    summary = f"sync_decision required: basis={new_decision.basis}"
    if detail:
        summary = f"{summary} detail={detail}"
    rs.phase = PHASE_AWAITING_USER
    rs.pending = Pending(
        kind=PENDING_KIND_SYNC_DECISION,
        summary=summary.strip(),
        payload={
            "decision_id": new_decision.decision_id,
            "basis": new_decision.basis,
        },
    )
    rs.refresh_recovery()
    rs_store.save(rs)
    return new_decision

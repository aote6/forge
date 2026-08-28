"""SyncDecision — 用户/主 AI 对一次同步策略点的显式决议。

落点：`<project_root>/.forge/sync_decision.json`

契约：docs/RUNTIME_STATE_CONTRACT.md §3

与 Mutation Confirmation（PendingAction / Execution Pause）严格分离：
  - SyncDecision 粒度是同步策略点，触发于 detect()=CONFLICT|FAST_FORWARD
  - Mutation Confirmation 粒度是单次写工具，不持久化

R2 最小闭环：数据对象 + 持久化；由 Runtime.sync_status 打开 pending，
resolve_sync_decision 关闭；Gate 在 pending 期间拒写与 forge_sync 推进。
"""
from __future__ import annotations

import json
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
)

DECISION_REQUIRED_STATUSES = frozenset(
    {
        CONFLICT,
        FAST_FORWARD_DISK_TO_WORLD,
        FAST_FORWARD_WORLD_TO_DISK,
    }
)


@dataclass
class SyncDecision:
    """一次同步策略点的决议（可持久化）。"""

    decision_id: str
    basis: str
    direction: str | None = None
    status: str = STATUS_PENDING
    created_at: float = 0.0
    decided_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "basis": self.basis,
            "direction": self.direction,
            "status": self.status,
            "created_at": self.created_at,
            "decided_at": self.decided_at,
        }

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
        return cls(
            decision_id=did,
            basis=basis,
            direction=direction,
            status=status,
            created_at=created_at,
            decided_at=decided_at,
        )

    @classmethod
    def new_pending(cls, basis: str) -> SyncDecision:
        return cls(
            decision_id=f"sd_{uuid.uuid4().hex[:12]}",
            basis=str(basis),
            direction=None,
            status=STATUS_PENDING,
            created_at=time.time(),
            decided_at=None,
        )

    def apply_direction(self, direction: str) -> None:
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

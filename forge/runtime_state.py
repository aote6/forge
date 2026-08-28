"""RuntimeState — 主 Runtime 执行生命周期的最小持久化真相。

落点：`<project_root>/.forge/runtime_state.json`

契约：docs/RUNTIME_STATE_CONTRACT.md（R1 最小闭环）

字段：
  - phase: 当前阶段（见 PHASE_*）
  - active_subtask_id: 正在跑的子任务 id，或 None
  - pending: 仅持久化 kind=sync_decision；execution_pause 不进本文件

recovery 不写入本文件。启动时由 derive_recovery(phase, pending) 推导。

R1 不做：
  - durable pause
  - 子循环栈恢复
  - Gate 消费 pending
  - SyncDecision 对象
"""
from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# ── phase 枚举（契约 §2.2）────────────────────────────────────────
PHASE_IDLE = "IDLE"
PHASE_DISPATCHING = "DISPATCHING"
PHASE_RUNNING_SUBTASK = "RUNNING_SUBTASK"
PHASE_PAUSED_SUBTASK = "PAUSED_SUBTASK"
PHASE_AWAITING_USER = "AWAITING_USER"
PHASE_COMPLETED = "COMPLETED"
PHASE_BLOCKED = "BLOCKED"
PHASE_ABORTED = "ABORTED"

VALID_PHASES = frozenset(
    {
        PHASE_IDLE,
        PHASE_DISPATCHING,
        PHASE_RUNNING_SUBTASK,
        PHASE_PAUSED_SUBTASK,
        PHASE_AWAITING_USER,
        PHASE_COMPLETED,
        PHASE_BLOCKED,
        PHASE_ABORTED,
    }
)

# ── pending.kind（R1 持久化只允许 sync_decision）──────────────────
PENDING_KIND_SYNC_DECISION = "sync_decision"
# execution_pause 仅进程内，永不写入 runtime_state.json

# ── recovery.mode（契约 §2.4，不持久化）───────────────────────────
RECOVERY_NONE = "none"
RECOVERY_DECISION_REQUIRED = "decision_required"
RECOVERY_ABORT = "abort"

RUNTIME_STATE_RELATIVE = Path(".forge") / "runtime_state.json"


@dataclass
class Pending:
    """RuntimeState.pending — 仅 sync_decision 可持久化。"""

    kind: str
    summary: str = ""
    payload: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "summary": self.summary or "",
            "payload": dict(self.payload or {}),
        }

    @classmethod
    def from_dict(cls, data: Any) -> Pending | None:
        if data is None:
            return None
        if not isinstance(data, dict):
            return None
        kind = str(data.get("kind") or "").strip()
        # R1: only persist/accept sync_decision on the durable path
        if kind != PENDING_KIND_SYNC_DECISION:
            return None
        payload = data.get("payload")
        if not isinstance(payload, dict):
            payload = {}
        return cls(
            kind=PENDING_KIND_SYNC_DECISION,
            summary=str(data.get("summary") or ""),
            payload=dict(payload),
        )


@dataclass(frozen=True)
class Recovery:
    """启动时推导的恢复模式；不写入 runtime_state.json。"""

    mode: str  # none | decision_required | abort
    reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"mode": self.mode, "reason": self.reason}


def derive_recovery(
    phase: str,
    pending: Pending | None,
) -> Recovery:
    """从持久化的 phase + pending 推导 recovery（契约 §2.4）。

    不执行任何恢复动作，只表达“上次是否留下未完成状态”。
    """
    phase = (phase or PHASE_IDLE).strip() or PHASE_IDLE
    if phase == PHASE_IDLE:
        return Recovery(mode=RECOVERY_NONE, reason=None)
    if phase in (PHASE_COMPLETED, PHASE_BLOCKED, PHASE_ABORTED):
        return Recovery(mode=RECOVERY_NONE, reason=None)
    if phase == PHASE_AWAITING_USER:
        if pending is not None and pending.kind == PENDING_KIND_SYNC_DECISION:
            return Recovery(
                mode=RECOVERY_DECISION_REQUIRED,
                reason="pending sync_decision awaits user resolution",
            )
        # awaiting user without durable pending → treat as clean start
        return Recovery(
            mode=RECOVERY_NONE,
            reason="AWAITING_USER without durable pending; starting clean",
        )
    if phase == PHASE_DISPATCHING:
        return Recovery(
            mode=RECOVERY_ABORT,
            reason="DISPATCHING not resumable; re-dispatch required",
        )
    if phase == PHASE_RUNNING_SUBTASK:
        return Recovery(
            mode=RECOVERY_ABORT,
            reason="RUNNING_SUBTASK stack not recoverable",
        )
    if phase == PHASE_PAUSED_SUBTASK:
        return Recovery(
            mode=RECOVERY_ABORT,
            reason="PAUSED_SUBTASK in-process pause not recoverable",
        )
    # Unknown phase → abort (safe)
    return Recovery(
        mode=RECOVERY_ABORT,
        reason=f"unknown phase {phase!r}; not recoverable",
    )


def _normalize_phase(raw: Any) -> str:
    s = str(raw or "").strip()
    if s in VALID_PHASES:
        return s
    return PHASE_IDLE


@dataclass
class RuntimeState:
    """主 Runtime 执行生命周期真相（最小字段）。"""

    phase: str = PHASE_IDLE
    active_subtask_id: str | None = None
    pending: Pending | None = None
    # derived at load/start; never serialized
    recovery: Recovery = field(default_factory=lambda: Recovery(mode=RECOVERY_NONE))

    def to_dict(self) -> dict[str, Any]:
        """可序列化视图；不含 recovery。"""
        return {
            "phase": self.phase,
            "active_subtask_id": self.active_subtask_id,
            "pending": self.pending.to_dict() if self.pending is not None else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> RuntimeState:
        if not data or not isinstance(data, dict):
            st = cls()
            st.recovery = derive_recovery(st.phase, st.pending)
            return st
        phase = _normalize_phase(data.get("phase"))
        sid = data.get("active_subtask_id")
        if sid is not None:
            sid = str(sid).strip() or None
        pending = Pending.from_dict(data.get("pending"))
        st = cls(phase=phase, active_subtask_id=sid, pending=pending)
        st.recovery = derive_recovery(st.phase, st.pending)
        return st

    def refresh_recovery(self) -> Recovery:
        self.recovery = derive_recovery(self.phase, self.pending)
        return self.recovery


class RuntimeStateStore:
    """`.forge/runtime_state.json` 的加载 / 保存。

    行为对齐 SyncState：
      - 文件不存在 → 默认 IDLE
      - 空/损坏 → 标记 .broken，回到默认
      - 原子写：先写 .tmp 再 replace
    """

    def __init__(self, project_root: str | Path):
        self.project_root = str(Path(project_root).expanduser().resolve())
        self._dir = Path(self.project_root) / ".forge"
        self._file = self._dir / "runtime_state.json"

    @property
    def path(self) -> Path:
        return self._file

    def load(self) -> RuntimeState:
        if not self._file.exists():
            st = RuntimeState()
            st.recovery = derive_recovery(st.phase, st.pending)
            return st
        try:
            raw = self._file.read_text(encoding="utf-8").strip()
            if not raw:
                return self._recover_broken("empty file")
            data = json.loads(raw)
            if not isinstance(data, dict):
                return self._recover_broken("not a JSON object")
            return RuntimeState.from_dict(data)
        except Exception as e:
            return self._recover_broken(str(e))

    def save(self, state: RuntimeState) -> None:
        """原子写入。不写入 recovery。"""
        self._dir.mkdir(parents=True, exist_ok=True)
        tmp = self._file.with_suffix(".tmp")
        payload = state.to_dict()
        text = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(self._file)

    def _recover_broken(self, reason: str) -> RuntimeState:
        print(
            f"[runtime_state] load failed ({reason}), starting fresh",
            file=sys.stderr,
        )
        try:
            if self._file.exists():
                broken = self._file.with_suffix(".json.broken")
                self._file.rename(broken)
        except Exception as e:
            print(
                f"[runtime_state] rename to .broken failed: {e}",
                file=sys.stderr,
            )
        st = RuntimeState()
        st.recovery = derive_recovery(st.phase, st.pending)
        return st

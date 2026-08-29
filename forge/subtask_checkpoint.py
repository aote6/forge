"""SubtaskCheckpoint — durable pause pointer for subagent recovery.

落点：<project_root>/.forge/subtask_checkpoint.json（单槽位）

与 forge/projections/checkpoint.py（Veritas projection 水位）和
runtime._progress_checkpoint_text（瞬时提示）无关。

契约：docs/DURABLE_PAUSE_DESIGN.md
"""
from __future__ import annotations

import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ── SubtaskRecovery.mode（启动时派生，不持久化）──────────────────
SUBTASK_RECOVERY_NONE = "none"
SUBTASK_RECOVERY_DECISION_REQUIRED = "decision_required"
SUBTASK_RECOVERY_INCONSISTENT = "inconsistent"

CHECKPOINT_RELATIVE = Path(".forge") / "subtask_checkpoint.json"
MAX_RESUME_ATTEMPTS = 3


@dataclass
class SubtaskCheckpoint:
    """Minimal durable pointer for a interrupted subtask."""

    subtask_id: str
    task: dict[str, Any]
    last_tool_call_id: str
    attempt_count: int = 0
    updated_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "subtask_id": self.subtask_id,
            "task": dict(self.task or {}),
            "last_tool_call_id": self.last_tool_call_id,
            "attempt_count": int(self.attempt_count or 0),
            "updated_at": float(self.updated_at or 0.0),
        }

    @classmethod
    def from_dict(cls, data: Any) -> SubtaskCheckpoint | None:
        if not isinstance(data, dict):
            return None
        sid = str(data.get("subtask_id") or "").strip()
        last_id = str(data.get("last_tool_call_id") or "").strip()
        if not sid or not last_id:
            return None
        task = data.get("task")
        if not isinstance(task, dict):
            task = {}
        try:
            attempt = int(data.get("attempt_count") or 0)
        except (TypeError, ValueError):
            attempt = 0
        try:
            updated = float(data.get("updated_at") or 0.0)
        except (TypeError, ValueError):
            updated = 0.0
        return cls(
            subtask_id=sid,
            task=dict(task),
            last_tool_call_id=last_id,
            attempt_count=max(0, attempt),
            updated_at=updated,
        )


@dataclass(frozen=True)
class SubtaskRecovery:
    """Derived at Runtime start; never persisted."""

    mode: str  # none | decision_required | inconsistent
    checkpoint: SubtaskCheckpoint | None = None
    reason: str | None = None
    # For INCONSISTENT: whether ToolCallRecord fact check passed
    fact_valid: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "checkpoint": self.checkpoint.to_dict() if self.checkpoint else None,
            "reason": self.reason,
            "fact_valid": self.fact_valid,
        }


def derive_subtask_recovery(
    checkpoint: SubtaskCheckpoint | None,
    phase: str,
    active_subtask_id: str | None,
) -> SubtaskRecovery:
    """Cross-check checkpoint against RuntimeState (design §3.1).

    DISPATCHING / PAUSED_SUBTASK are retained in the phase enum but do not
    participate in practical DECISION_REQUIRED (design §3.1 DECISION).
    Practical consistent path: phase == RUNNING_SUBTASK and active == C.
    """
    if checkpoint is None:
        return SubtaskRecovery(mode=SUBTASK_RECOVERY_NONE)

    phase = (phase or "").strip()
    active = (active_subtask_id or "").strip() or None
    cid = checkpoint.subtask_id

    # Practical DECISION_REQUIRED: RUNNING_SUBTASK + matching active id.
    # Design explicitly excludes DISPATCHING/PAUSED from reachable checks.
    if phase == "RUNNING_SUBTASK" and active == cid:
        return SubtaskRecovery(
            mode=SUBTASK_RECOVERY_DECISION_REQUIRED,
            checkpoint=checkpoint,
            reason="checkpoint matches RUNNING_SUBTASK + active_subtask_id",
        )

    return SubtaskRecovery(
        mode=SUBTASK_RECOVERY_INCONSISTENT,
        checkpoint=checkpoint,
        reason=(
            f"checkpoint subtask_id={cid!r} vs phase={phase!r} "
            f"active={active!r}"
        ),
        fact_valid=False,  # filled by Runtime after ToolCallRecord check
    )


def validate_checkpoint_facts(
    project_root: str | Path,
    checkpoint: SubtaskCheckpoint,
) -> bool:
    """INCONSISTENT fact check (design §7).

    合法恢复边界 := get_record(last_tool_call_id) 存在
    且 record.subtask_id == checkpoint.subtask_id.
    """
    from forge.tool_call_record import get_record

    rec = get_record(project_root, checkpoint.last_tool_call_id)
    if not rec:
        return False
    return str(rec.get("subtask_id") or "") == checkpoint.subtask_id


def build_prior_facts_summary(
    project_root: str | Path,
    subtask_id: str,
) -> str:
    """Read-only fact summary from ALL ToolCallRecords for subtask_id.

    Only tool_call_id / tool_name / status — never CONCLUSION/EVIDENCE text.
    """
    from forge.tool_call_record import list_records_for_subtask

    records = list_records_for_subtask(project_root, subtask_id)
    if not records:
        return ""
    lines = ["[PRIOR_FACTS] completed tool calls for this subtask (do not re-execute):"]
    for r in records:
        tid = r.get("tool_call_id") or ""
        name = r.get("tool_name") or ""
        status = r.get("status") or ""
        lines.append(f"- tool_call_id={tid} tool_name={name} status={status}")
    return "\n".join(lines)


class SubtaskCheckpointStore:
    """Atomic load/save/clear for .forge/subtask_checkpoint.json."""

    def __init__(self, project_root: str | Path):
        self.project_root = str(Path(project_root).expanduser().resolve())
        self._dir = Path(self.project_root) / ".forge"
        self._file = self._dir / "subtask_checkpoint.json"

    @property
    def path(self) -> Path:
        return self._file

    def load(self) -> SubtaskCheckpoint | None:
        """Missing / empty / corrupt → None (safe discard, never raise)."""
        if not self._file.exists():
            return None
        try:
            raw = self._file.read_text(encoding="utf-8").strip()
            if not raw:
                return None
            data = json.loads(raw)
            return SubtaskCheckpoint.from_dict(data)
        except Exception as e:
            print(
                f"[subtask_checkpoint] load failed ({e}), discarding",
                file=sys.stderr,
            )
            try:
                broken = self._file.with_suffix(".json.broken")
                self._file.rename(broken)
            except Exception:
                pass
            return None

    def save(self, checkpoint: SubtaskCheckpoint) -> bool:
        """Atomic write: tmp + replace. Returns True on success."""
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            tmp = self._file.with_suffix(".tmp")
            text = json.dumps(checkpoint.to_dict(), ensure_ascii=False, indent=2) + "\n"
            tmp.write_text(text, encoding="utf-8")
            tmp.replace(self._file)
            return True
        except Exception as e:
            print(f"[subtask_checkpoint] save failed: {e}", file=sys.stderr)
            return False

    def clear(self) -> bool:
        """Remove checkpoint file. Returns True if gone or never existed."""
        try:
            if self._file.exists():
                self._file.unlink()
            return True
        except Exception as e:
            print(f"[subtask_checkpoint] clear failed: {e}", file=sys.stderr)
            return False

    def update_after_tool(
        self,
        *,
        subtask_id: str,
        task_dict: dict[str, Any],
        last_tool_call_id: str,
        attempt_count: int | None = None,
    ) -> bool:
        """Create or advance checkpoint after a successful tool + Layer B pass."""
        existing = self.load()
        attempts = 0
        if existing is not None and existing.subtask_id == subtask_id:
            attempts = existing.attempt_count
        if attempt_count is not None:
            attempts = attempt_count
        cp = SubtaskCheckpoint(
            subtask_id=subtask_id,
            task=dict(task_dict or {}),
            last_tool_call_id=last_tool_call_id,
            attempt_count=attempts,
            updated_at=time.time(),
        )
        return self.save(cp)

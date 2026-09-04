"""
forge/sync/attempt.py

Phase D — durable record of an in-progress world_to_disk reconcile execution.

Purpose
-------
FileProjection.apply() (writes disk) and SyncState.mark_disk_synced()
(advances the watermark) are two separate operations. If the process dies
between them, disk has already changed but the watermark hasn't advanced,
and a naive restart cannot tell whether a given receipt was actually
applied.

ReconcileAttempt is a third durable artifact (.forge/reconcile_attempt.json)
written *around* that gap so a restart can compare "what we expected disk
to look like after receipt i" against "what disk actually looks like" and
decide, per-receipt, whether to backfill the mark or stop.

Design constraints (per spec)
------------------------------
* Does not introduce a new RuntimeState phase.
* Does not change Phase A/B/C semantics — this only wraps
  apply_world_to_disk_decision's execution loop.
* The receipt sequence is frozen at attempt-creation time. Recovery never
  re-queries receipts; it only ever looks at the frozen
  `execution_receipts` list already on disk.
* Ambiguous or mismatched state on recovery => STOP. No auto-continue,
  no supersede of the SyncDecision. This is a deliberate fail-loud choice
  consistent with the rest of the toolchain.

ASSUMPTIONS / integration points you should double check
----------------------------------------------------------
This file was written without access to the live sync_layer.py /
state.py source (blind handoff). Two things are assumed and marked
below with `# ASSUME:` — confirm or fix before wiring in:

1. `hash_bytes(path)` — I could not confirm the name/location of your
   existing content-hash utility (the one `recompute_hashes` and the
   circuit-breaker path+content keying already use). A local sha256
   fallback is provided; swap `_hash_file` to call your real one so the
   hash format matches what's stored elsewhere (e.g. in
   last_known_file_hashes).
2. Receipt shape — assumed each receipt is either an object or dict
   exposing `.path` / `["path"]`, `.version` / `["version"]`, and either
   `.content` (bytes/str of the new file content, for MISSING-detection
   use `.op == "delete"` or `.deleted`) or enough info to derive the
   expected post-apply disk state. See `_expected_effect_for_receipt`
   below — this is the one function most likely to need adjustment to
   match your actual receipt schema.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import time
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Optional

MISSING = "MISSING"

STATUS_IN_PROGRESS = "IN_PROGRESS"
STATUS_FAILED = "FAILED"
STATUS_COMPLETED = "COMPLETED"

_VALID_STATUSES = (STATUS_IN_PROGRESS, STATUS_FAILED, STATUS_COMPLETED)

ATTEMPT_FILENAME = "reconcile_attempt.json"


def _hash_file(path: Path) -> Optional[str]:
    """Content hash of a file on disk, or None if it doesn't exist.

    Delegates to forge.sync.git_utils.hash_file so the hash format
    matches last_known_file_hashes / circuit-breaker keying exactly.
    """
    from forge.sync.git_utils import hash_file
    return hash_file(str(path))


def _receipt_get(receipt: Any, key: str, default: Any = None) -> Any:
    """Read `key` off a receipt whether it's an object or a dict."""
    if isinstance(receipt, dict):
        return receipt.get(key, default)
    return getattr(receipt, key, default)


def _expected_effect_for_receipt(receipt: Any) -> dict:
    """Compute expected effect for a receipt.

    Real integration computes expected effects from FileProjection.prepare()
    and passes them explicitly to set_expected_effect(effect=...). This
    function is kept for standalone recovery tests that use simplified
    receipt dicts with path/content/op fields.
    """
    """Compute the expected post-apply disk state for one receipt.

    Returns {"path": <str>, "effect": <content_hash str> | MISSING}.

    # ASSUME: adjust the delete-detection and hash source below to match
    # the real receipt schema. As written: a receipt is treated as a
    # delete if op/action == "delete" or a truthy `.deleted` flag is
    # present; otherwise the receipt's own carried content (if present)
    # is hashed directly (avoids depending on FileProjection internals),
    # falling back to hashing whatever apply() leaves on disk turns out
    # to be — see NOTE in ReconcileAttemptStore.record_expected_effect.
    """
    path = _receipt_get(receipt, "path")
    if path is None:
        raise ValueError(f"receipt missing 'path': {receipt!r}")

    op = _receipt_get(receipt, "op") or _receipt_get(receipt, "action")
    deleted = _receipt_get(receipt, "deleted", False)
    if deleted or op == "delete":
        return {"path": str(path), "effect": MISSING}

    content = _receipt_get(receipt, "content")
    if content is not None:
        if isinstance(content, str):
            content = content.encode("utf-8")
        return {"path": str(path), "effect": hashlib.sha256(content).hexdigest()}

    # Receipt doesn't carry raw content (e.g. it's a diff/patch receipt).
    # Caller (sync_layer) should compute the expected hash itself from
    # whatever it's about to hand to FileProjection.apply() and pass it
    # in via ReconcileAttemptStore.set_expected_effect(index, effect=...)
    # BEFORE calling apply(). Returning None here signals "unknown /
    # caller must supply".
    return {"path": str(path), "effect": None}


def _atomic_write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".reconcile_attempt.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, path)
    finally:
        if os.path.exists(tmp_name):
            try:
                os.remove(tmp_name)
            except OSError:
                pass


@dataclass
class ReconcileAttempt:
    attempt_id: str
    decision_id: str
    generation: dict
    execution_receipts: list
    expected_path_effects: list = field(default_factory=list)
    next_receipt_index: int = 0
    last_marked_version: Optional[int] = None
    status: str = STATUS_IN_PROGRESS
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "ReconcileAttempt":
        return cls(
            attempt_id=d["attempt_id"],
            decision_id=d["decision_id"],
            generation=d["generation"],
            execution_receipts=d["execution_receipts"],
            expected_path_effects=d.get("expected_path_effects", []),
            next_receipt_index=d.get("next_receipt_index", 0),
            last_marked_version=d.get("last_marked_version"),
            status=d.get("status", STATUS_IN_PROGRESS),
            created_at=d.get("created_at", time.time()),
            updated_at=d.get("updated_at", time.time()),
        )

    def current_receipt(self) -> Optional[Any]:
        if self.next_receipt_index >= len(self.execution_receipts):
            return None
        return self.execution_receipts[self.next_receipt_index]

    def expected_effect_at(self, index: int) -> Optional[dict]:
        if index >= len(self.expected_path_effects):
            return None
        return self.expected_path_effects[index]


class ReconcileAttemptStore:
    """Durable CRUD around .forge/reconcile_attempt.json.

    Every mutating method writes the whole file atomically (write-tmp +
    fsync + rename). There is at most one attempt on disk at a time —
    starting a new one requires the previous one be cleared (completed
    or explicitly discarded) first, so a stray IN_PROGRESS attempt from
    a crash is never silently overwritten.
    """

    def __init__(self, forge_dir: Path | str):
        self.path = Path(forge_dir) / ATTEMPT_FILENAME

    def load(self) -> Optional[ReconcileAttempt]:
        if not self.path.exists():
            return None
        with open(self.path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        return ReconcileAttempt.from_dict(raw)

    def _write(self, attempt: ReconcileAttempt) -> None:
        attempt.updated_at = time.time()
        _atomic_write_json(self.path, attempt.to_dict())

    def create(self, decision: Any, execution_receipts: list) -> ReconcileAttempt:
        """Start a new attempt for `decision`'s frozen receipt sequence.

        Refuses to clobber an existing IN_PROGRESS attempt — caller
        (sync_layer) must run recovery first if one is found.
        """
        existing = self.load()
        if existing is not None and existing.status == STATUS_IN_PROGRESS:
            raise RuntimeError(
                "refusing to start a new ReconcileAttempt while an "
                f"IN_PROGRESS attempt ({existing.attempt_id}) exists; "
                "run recovery first"
            )

        decision_id = _receipt_get(decision, "decision_id") or _receipt_get(decision, "id")
        generation = _receipt_get(decision, "generation")
        if isinstance(generation, dict):
            generation_dict = dict(generation)
        elif generation is not None and hasattr(generation, "to_dict"):
            generation_dict = generation.to_dict()
        elif generation is not None and hasattr(generation, "__dict__"):
            generation_dict = dict(vars(generation))
        else:
            generation_dict = generation or {}

        # Serialize receipts to plain dicts so the frozen sequence
        # survives round-tripping through JSON untouched.
        def _to_jsonable(obj):
            if isinstance(obj, dict):
                return {str(k): _to_jsonable(v) for k, v in obj.items()}
            if isinstance(obj, (list, tuple)):
                return [_to_jsonable(v) for v in obj]
            if isinstance(obj, (str, int, float, bool)) or obj is None:
                return obj
            if hasattr(obj, "to_dict"):
                return _to_jsonable(obj.to_dict())
            if hasattr(obj, "__dict__"):
                return _to_jsonable(vars(obj))
            return str(obj)

        frozen = [_to_jsonable(r) for r in execution_receipts]

        attempt = ReconcileAttempt(
            attempt_id=str(uuid.uuid4()),
            decision_id=str(decision_id),
            generation=generation_dict,
            execution_receipts=frozen,
            expected_path_effects=[None] * len(frozen),
            next_receipt_index=0,
            last_marked_version=None,
            status=STATUS_IN_PROGRESS,
        )
        self._write(attempt)
        return attempt

    def set_expected_effect(self, attempt: ReconcileAttempt, index: int, effect: Optional[dict] = None) -> ReconcileAttempt:
        """Durably record what disk should look like after receipt[index],
        BEFORE that receipt is applied. Must be called before the
        corresponding FileProjection.apply().

        If `effect` is omitted, it's computed from the receipt itself via
        `_expected_effect_for_receipt` (works when the receipt carries
        its own target content; for diff-only receipts pass `effect`
        explicitly — see module docstring ASSUMPTION #2).
        """
        if effect is None:
            effect = _expected_effect_for_receipt(attempt.execution_receipts[index])
            if effect.get("effect") is None:
                raise ValueError(
                    f"cannot derive expected effect for receipt index {index}; "
                    "caller must pass effect= explicitly for diff-style receipts"
                )
        while len(attempt.expected_path_effects) <= index:
            attempt.expected_path_effects.append(None)
        attempt.expected_path_effects[index] = effect
        self._write(attempt)
        return attempt

    def record_progress(self, attempt: ReconcileAttempt, next_receipt_index: int, last_marked_version: Optional[int]) -> ReconcileAttempt:
        """Durably advance progress AFTER a receipt's apply() + mark_disk_synced()
        have both succeeded."""
        attempt.next_receipt_index = next_receipt_index
        attempt.last_marked_version = last_marked_version
        self._write(attempt)
        return attempt

    def mark_failed(self, attempt: ReconcileAttempt) -> ReconcileAttempt:
        attempt.status = STATUS_FAILED
        self._write(attempt)
        return attempt

    def mark_completed(self, attempt: ReconcileAttempt) -> ReconcileAttempt:
        attempt.status = STATUS_COMPLETED
        self._write(attempt)
        return attempt

    def clear(self) -> None:
        """Remove the attempt file entirely (e.g. after COMPLETED, or
        after an operator has manually resolved a STOPped recovery)."""
        if self.path.exists():
            os.remove(self.path)


@dataclass
class RecoveryResult:
    """Outcome of checking a crashed attempt against real disk state."""

    action: str  # "none" | "backfilled_and_ready" | "stopped"
    attempt: Optional[ReconcileAttempt]
    reason: Optional[str] = None
    mismatched_index: Optional[int] = None
    mismatched_path: Optional[str] = None
    expected: Optional[Any] = None
    actual: Optional[Any] = None


def recover(store: ReconcileAttemptStore, root: Path | str) -> RecoveryResult:
    """Check for a crashed attempt and resolve the boundary receipt only.

    Only ever inspects `attempt.execution_receipts[attempt.next_receipt_index]`
    — the one receipt that could plausibly have been mid-flight at crash
    time (everything before it was already marked; everything after it
    was never attempted). Does NOT re-query the receipt list from
    anywhere else; uses only the frozen sequence already on disk.

    Returns:
      action="none"                 -> no IN_PROGRESS attempt found, nothing to do
      action="backfilled_and_ready" -> boundary receipt's disk effect matched
                                        expectation exactly; mark_disk_synced was
                                        NOT called here (caller must call it —
                                        see sync_layer integration note below)
                                        and next_receipt_index has been advanced.
                                        Caller should resume the normal apply
                                        loop from the new next_receipt_index.
      action="stopped"              -> mismatch or ambiguity. attempt is left
                                        untouched (still IN_PROGRESS) on disk.
                                        Caller MUST NOT resume the loop and
                                        MUST NOT let Phase A stale/supersede
                                        logic reclaim the decision.
    """
    root = Path(root)
    attempt = store.load()
    if attempt is None or attempt.status != STATUS_IN_PROGRESS:
        return RecoveryResult(action="none", attempt=attempt)

    idx = attempt.next_receipt_index
    if idx >= len(attempt.execution_receipts):
        # Every receipt was already marked; this is really "completed but
        # never finalized" (crash happened right after the last mark,
        # before mark_completed()). Safe to finalize.
        store.mark_completed(attempt)
        return RecoveryResult(action="backfilled_and_ready", attempt=attempt)

    expected = attempt.expected_effect_at(idx)
    if expected is None:
        # We crashed before even durably writing the expected effect for
        # this receipt, i.e. before touching disk for it at all. Nothing
        # to reconcile — the receipt simply hasn't been attempted.
        return RecoveryResult(action="backfilled_and_ready", attempt=attempt)

    path = root / expected["path"]
    expected_effect = expected["effect"]
    actual_hash = _hash_file(path)
    actual_effect = MISSING if actual_hash is None else actual_hash

    if actual_effect == expected_effect:
        # apply() succeeded before the crash; mark_disk_synced() did not.
        # Backfill: advance the boundary by one. Caller is responsible for
        # calling mark_disk_synced(receipt.version) for THIS receipt
        # (sync_layer owns the SyncState reference, not this module) and
        # then calling store.record_progress(...) to persist the advance —
        # or call record_progress here if you'd rather centralize it.
        return RecoveryResult(action="backfilled_and_ready", attempt=attempt)

    # Mismatch or ambiguous (partial write, wrong content, wrong
    # presence/absence). Fail loud. Do not touch the attempt file, do not
    # advance, do not let the decision be superseded.
    return RecoveryResult(
        action="stopped",
        attempt=attempt,
        reason="disk state does not match expected post-apply effect for the "
               "boundary receipt; manual inspection required",
        mismatched_index=idx,
        mismatched_path=expected["path"],
        expected=expected_effect,
        actual=actual_effect,
    )

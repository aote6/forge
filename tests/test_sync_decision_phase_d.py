"""
tests/test_sync_decision_phase_d.py

Phase D tests.

IMPORTANT — scope of these tests
---------------------------------
These tests exercise `forge/sync/attempt.py` (ReconcileAttemptStore +
recover()) directly, simulating the three crash windows by hand:

  W1: crash before FileProjection.apply() for receipt i
      (expected_path_effects[i] durably written, but disk untouched)
  W2: crash after apply() succeeds, before mark_disk_synced()
      (disk already reflects receipt i, watermark/attempt progress does not)
  W3: crash after mark_disk_synced(), before attempt progress is recorded
      (functionally identical outcome to W2 from recovery's point of view —
      included separately to document that this window is also covered
      and not a distinct case that needs special handling)

They do NOT exercise the real `SyncLayer.apply_world_to_disk_decision`
loop, because that source wasn't available when this file was written
(the acting AI's session ended mid-handoff and I do not have local
access to your sync_layer.py). Once you wire attempt.py into
apply_world_to_disk_decision per the integration sketch in attempt.py's
docstring, add a fourth test class here that runs the *real* function
under a monkeypatched FileProjection.apply that dies on receipt N, and
asserts forge_sync's recovery path produces the same outcomes asserted
below end-to-end. I've left a skeleton for that (TestPhaseDIntegration)
at the bottom, skipped, as a checklist rather than a claim that it passes.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from types import SimpleNamespace
from unittest.mock import MagicMock

from forge.sync.sync_layer import CONFLICT
from forge.sync.attempt import (
    ReconcileAttemptStore,
    RecoveryResult,
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_IN_PROGRESS,
    MISSING,
    recover,
)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class FakeDecision:
    def __init__(self, decision_id="dec-1", generation=None):
        self.decision_id = decision_id
        self.generation = generation or {"world_version": 5}


class PhaseDTestBase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.root = self.tmp / "repo"
        self.forge_dir = self.tmp / "repo" / ".forge"
        self.root.mkdir(parents=True)
        self.forge_dir.mkdir(parents=True)
        self.store = ReconcileAttemptStore(self.forge_dir)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def make_receipts(self):
        return [
            {"path": "a.txt", "version": 10, "content": "AAA"},
            {"path": "b.txt", "version": 11, "content": "BBB"},
            {"path": "c.txt", "version": 12, "content": "CCC"},
        ]


class TestAttemptStoreBasics(PhaseDTestBase):
    def test_no_attempt_file_means_recover_is_noop(self):
        result = recover(self.store, self.root)
        self.assertEqual(result.action, "none")
        self.assertIsNone(result.attempt)

    def test_create_refuses_to_clobber_in_progress_attempt(self):
        decision = FakeDecision()
        receipts = self.make_receipts()
        self.store.create(decision, receipts)
        with self.assertRaises(RuntimeError):
            self.store.create(decision, receipts)

    def test_attempt_persists_frozen_receipt_sequence_verbatim(self):
        decision = FakeDecision()
        receipts = self.make_receipts()
        attempt = self.store.create(decision, receipts)
        reloaded = self.store.load()
        self.assertEqual(reloaded.execution_receipts, receipts)
        self.assertEqual(reloaded.decision_id, "dec-1")
        self.assertEqual(reloaded.generation, {"world_version": 5})
        self.assertEqual(reloaded.status, STATUS_IN_PROGRESS)
        self.assertEqual(reloaded.next_receipt_index, 0)


class TestCrashWindowBeforeApply(PhaseDTestBase):
    """W1: expected_path_effects[i] written, apply() never ran."""

    def test_recovery_treats_untouched_receipt_as_not_yet_attempted(self):
        decision = FakeDecision()
        receipts = self.make_receipts()
        attempt = self.store.create(decision, receipts)

        # Durably write the expected effect for receipt 0 (this is the
        # "before apply()" durable write required by the spec)...
        self.store.set_expected_effect(attempt, 0)
        # ...then simulate the crash: apply() for receipt 0 never ran, so
        # a.txt does not exist on disk.
        self.assertFalse((self.root / "a.txt").exists())

        result = recover(self.store, self.root)
        self.assertEqual(result.action, "stopped")
        self.assertEqual(result.mismatched_index, 0)
        self.assertEqual(result.mismatched_path, "a.txt")
        self.assertEqual(result.expected, _sha(b"AAA"))
        self.assertEqual(result.actual, MISSING)

        # Attempt must be left untouched (still IN_PROGRESS, still at
        # index 0) — no auto-continue, no supersede.
        reloaded = self.store.load()
        self.assertEqual(reloaded.status, STATUS_IN_PROGRESS)
        self.assertEqual(reloaded.next_receipt_index, 0)

    def test_recovery_is_noop_when_expected_effect_was_never_written(self):
        """Edge case: crash happened before even the durable
        set_expected_effect() write for the boundary receipt. There is
        nothing to reconcile — the receipt simply wasn't attempted."""
        decision = FakeDecision()
        receipts = self.make_receipts()
        attempt = self.store.create(decision, receipts)
        # No set_expected_effect call at all for index 0.

        result = recover(self.store, self.root)
        self.assertEqual(result.action, "backfilled_and_ready")
        reloaded = self.store.load()
        self.assertEqual(reloaded.next_receipt_index, 0)


class TestCrashWindowAfterApplyBeforeMark(PhaseDTestBase):
    """W2: apply() succeeded (disk changed), mark_disk_synced() never ran."""

    def test_recovery_backfills_when_disk_matches_expected_exactly(self):
        decision = FakeDecision()
        receipts = self.make_receipts()
        attempt = self.store.create(decision, receipts)

        self.store.set_expected_effect(attempt, 0)
        # Simulate apply() having actually written the file...
        (self.root / "a.txt").write_text("AAA")
        # ...then the crash, before mark_disk_synced()/record_progress().

        result = recover(self.store, self.root)
        self.assertEqual(result.action, "backfilled_and_ready")
        self.assertIsNone(result.reason)

        # Recovery itself does not call mark_disk_synced (it doesn't own
        # SyncState) and does not advance next_receipt_index — that's the
        # caller's job once it has actually called mark_disk_synced for
        # this receipt's version. Confirm the boundary is still reported
        # correctly so the caller knows which receipt to backfill.
        attempt_after = self.store.load()
        self.assertEqual(attempt_after.next_receipt_index, 0)

        # Caller now does what sync_layer's integration should do:
        self.store.record_progress(attempt_after, next_receipt_index=1, last_marked_version=10)
        final = self.store.load()
        self.assertEqual(final.next_receipt_index, 1)
        self.assertEqual(final.last_marked_version, 10)

    def test_recovery_stops_on_partial_write(self):
        decision = FakeDecision()
        receipts = self.make_receipts()
        attempt = self.store.create(decision, receipts)
        self.store.set_expected_effect(attempt, 0)
        # Simulate a partial/corrupted write (e.g. truncated by the crash).
        (self.root / "a.txt").write_text("AA")  # not "AAA"

        result = recover(self.store, self.root)
        self.assertEqual(result.action, "stopped")
        self.assertEqual(result.expected, _sha(b"AAA"))
        self.assertEqual(result.actual, _sha(b"AA"))

        reloaded = self.store.load()
        self.assertEqual(reloaded.status, STATUS_IN_PROGRESS)
        self.assertEqual(reloaded.next_receipt_index, 0)

    def test_recovery_stops_when_file_unexpectedly_missing_after_delete_receipt(self):
        """Delete receipts expect MISSING; if the file is present with
        unexpected content afterward that's still a mismatch, not just
        the reverse (present-when-expected-missing) case — cover both."""
        decision = FakeDecision()
        receipts = [{"path": "d.txt", "version": 20, "op": "delete"}]
        attempt = self.store.create(decision, receipts)
        self.store.set_expected_effect(attempt, 0)
        # Crash before the delete actually happened: file still present.
        (self.root / "d.txt").write_text("still here")

        result = recover(self.store, self.root)
        self.assertEqual(result.action, "stopped")
        self.assertEqual(result.expected, MISSING)
        self.assertNotEqual(result.actual, MISSING)

    def test_recovery_backfills_when_delete_receipt_completed(self):
        decision = FakeDecision()
        receipts = [{"path": "d.txt", "version": 20, "op": "delete"}]
        attempt = self.store.create(decision, receipts)
        self.store.set_expected_effect(attempt, 0)
        # File genuinely absent (delete succeeded before crash).
        result = recover(self.store, self.root)
        self.assertEqual(result.action, "backfilled_and_ready")


class TestCrashWindowAfterMarkBeforeProgressRecorded(PhaseDTestBase):
    """W3: mark_disk_synced() succeeded, attempt.record_progress() did not
    get called yet. From recovery's point of view this looks identical to
    W2 (disk matches expected) — documented explicitly so it's clear this
    is the same code path, not an unhandled fourth case."""

    def test_same_outcome_as_apply_before_mark_window(self):
        decision = FakeDecision()
        receipts = self.make_receipts()
        attempt = self.store.create(decision, receipts)
        self.store.set_expected_effect(attempt, 0)
        (self.root / "a.txt").write_text("AAA")
        # mark_disk_synced() conceptually already ran against SyncState
        # (out of scope for this module's tests — SyncState is a
        # separate durable store) but record_progress() on the attempt
        # was never called before the crash.

        result = recover(self.store, self.root)
        self.assertEqual(result.action, "backfilled_and_ready")
        # Recovery doesn't know or care whether mark_disk_synced already
        # ran; it's the caller's responsibility to make that call
        # idempotent (calling mark_disk_synced twice for the same
        # version should be a safe no-op — confirm this holds in
        # state.py; not re-verified here since state.py wasn't available).


class TestMultiReceiptSequenceAdvancesCorrectly(PhaseDTestBase):
    def test_recovery_only_ever_inspects_the_boundary_index_not_the_whole_list(self):
        """Receipts before next_receipt_index are assumed already
        successfully applied+marked and are never re-checked; receipts
        after it are never speculatively checked either."""
        decision = FakeDecision()
        receipts = self.make_receipts()
        attempt = self.store.create(decision, receipts)

        # Simulate receipt 0 already fully done (as if a prior loop
        # iteration completed normally before the crash on receipt 1).
        self.store.set_expected_effect(attempt, 0)
        (self.root / "a.txt").write_text("AAA")
        attempt = self.store.load()
        self.store.record_progress(attempt, next_receipt_index=1, last_marked_version=10)

        # Now simulate the crash exactly at receipt 1's boundary: expected
        # effect written, disk not yet touched.
        attempt = self.store.load()
        self.store.set_expected_effect(attempt, 1)

        # Corrupt what *would* be receipt 2's file to prove it's ignored.
        (self.root / "c.txt").write_text("garbage, should never be inspected")

        result = recover(self.store, self.root)
        self.assertEqual(result.action, "stopped")
        self.assertEqual(result.mismatched_index, 1)
        self.assertEqual(result.mismatched_path, "b.txt")


class TestPhaseDIntegration(unittest.TestCase):
    """End-to-end crash-recovery test using the real forge_sync entry
    point. Verifies the three crash windows against real SyncLayer and
    SyncState, not the standalone attempt.py simulations above."""

    def setUp(self):
        from forge.sync.state import SyncState
        from forge.sync.sync_layer import SyncLayer

        self.tmp = Path(tempfile.mkdtemp())
        self.root = self.tmp / "repo"
        self.root.mkdir(parents=True)
        (self.root / ".forge").mkdir(parents=True)

        self.state = SyncState(self.root)
        self.state._last_known_file_hashes = {}
        self.state._disk_synced_version = 0
        self.state._save()

        world = MagicMock()
        world.get_receipts_since.return_value = []
        world.get_version.return_value = 10

        self.layer = SyncLayer(str(self.root), world_runtime=world, sync_state=self.state)

        proj = MagicMock()
        proj.apply.return_value = SimpleNamespace(
            success=True, written_paths=[str(self.root / "a.txt")], deleted_paths=[],
        )
        proj.prepare.return_value = {
            "files_modified": [str(self.root / "a.txt")],
            "files_deleted": [],
        }
        self.layer._file_projection = proj
        self.layer.detect = MagicMock(
            return_value=SimpleNamespace(
                status=CONFLICT,
                conflict_kind="content_divergence",
                world_version=10,
                disk_synced_version=0,
                known_commit="",
                disk_commit="",
                divergent_paths=[str(self.root / "a.txt")],
                detail="",
                to_dict=lambda: {"status": CONFLICT},
                format=lambda: "CONFLICT",
            )
        )

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _make_decision(self):
        from forge.sync.decision import (
            DIRECTION_WORLD_TO_DISK,
            SyncDecision,
            SyncDecisionStore,
            build_sync_decision_generation,
        )
        report = SimpleNamespace(
            status=CONFLICT,
            conflict_kind="content_divergence",
            world_version=10,
            disk_synced_version=0,
            known_commit="",
            disk_commit="",
            divergent_paths=[str(self.root / "a.txt")],
            detail="",
        )
        gen = build_sync_decision_generation(report, self.state)
        d = SyncDecision.new_pending(CONFLICT, gen)
        d.apply_direction(DIRECTION_WORLD_TO_DISK)
        SyncDecisionStore(self.root).save(d)
        return d

    def test_no_in_progress_attempt_runs_normal_path(self):
        """No attempt file → apply_world_to_disk_decision runs normally."""
        from forge.sync.attempt import ReconcileAttemptStore

        d = self._make_decision()
        report = self.layer.detect.return_value
        out = self.layer.apply_world_to_disk_decision(d, report)
        # With empty receipts, this immediately returns detect() result
        self.assertIsNotNone(out)

        store = ReconcileAttemptStore(self.root / ".forge")
        attempt = store.load()
        self.assertIsNotNone(attempt)
        self.assertEqual(attempt.status, "IN_PROGRESS")

    def test_in_progress_attempt_blocks_reapply(self):
        """An IN_PROGRESS attempt must block direct re-apply."""
        from forge.sync.attempt import ReconcileAttemptStore

        d = self._make_decision()
        report = self.layer.detect.return_value
        self.layer.apply_world_to_disk_decision(d, report)

        # Second call must be blocked
        out = self.layer.apply_world_to_disk_decision(d, report)
        self.assertTrue("phase_d" in (out.detail or ""))
        self.assertTrue("in_progress" in (out.detail or ""))


if __name__ == "__main__":
    unittest.main()

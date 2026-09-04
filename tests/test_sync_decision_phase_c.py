"""Phase C: world_to_disk per-receipt + partial DECIDED not superseded."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from forge.sync.decision import (
    APPLICABLE,
    DIRECTION_WORLD_TO_DISK,
    PARTIAL_EXECUTION,
    STATUS_DECIDED,
    STALE,
    SyncDecision,
    SyncDecisionStore,
    build_sync_decision_generation,
    classify_decision_applicability,
)
from forge.sync.state import SyncState
from forge.sync.sync_layer import CONFLICT, IN_SYNC, SyncLayer, SyncReport
from forge.tools import make_tools


def _report(path: str, **kw):
    base = dict(
        status=CONFLICT,
        conflict_kind="content_divergence",
        world_version=10,
        disk_synced_version=5,
        known_commit="k",
        disk_commit="d",
        divergent_paths=[path],
        detail="c",
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_classify_partial_when_mark_count_and_dsv_drift(tmp_path: Path):
    f = tmp_path / "m.txt"
    f.write_text("v1", encoding="utf-8")
    st = SyncState(tmp_path)
    st._last_known_file_hashes = {str(f): "x"}
    st._disk_synced_version = 5
    st._save()
    report = _report(str(f), disk_synced_version=5)
    gen = build_sync_decision_generation(report, st)
    d = SyncDecision.new_pending(CONFLICT, gen)
    d.apply_direction(DIRECTION_WORLD_TO_DISK)
    d.mark_count = 1
    d.last_marked_version = 7
    # simulate watermark advanced after partial
    st._disk_synced_version = 7
    st._save()
    report2 = _report(str(f), disk_synced_version=7)
    kind = classify_decision_applicability(d, report2, st)
    assert kind == PARTIAL_EXECUTION
    assert kind != STALE


def test_stale_supersede_blocked_when_mark_count_positive(tmp_path: Path):
    f = tmp_path / "m.txt"
    f.write_text("v1", encoding="utf-8")
    st = SyncState(tmp_path)
    st._last_known_file_hashes = {str(f): "x"}
    st._disk_synced_version = 7
    st._save()
    report = _report(str(f), disk_synced_version=5, world_version=10)
    gen = build_sync_decision_generation(
        _report(str(f), disk_synced_version=5), st
    )
    # fix gen dsv to 5 while state is 7
    gen["disk_synced_version"] = 5
    gen["world_version"] = 10
    d = SyncDecision.new_pending(CONFLICT, gen)
    d.apply_direction(DIRECTION_WORLD_TO_DISK)
    d.mark_count = 2
    SyncDecisionStore(tmp_path).save(d)
    old_id = d.decision_id

    detect = SimpleNamespace(
        status=CONFLICT,
        conflict_kind="content_divergence",
        world_version=10,
        disk_synced_version=7,
        known_commit="k",
        disk_commit="d",
        divergent_paths=[str(f)],
        detail="c",
        to_dict=lambda: {"status": CONFLICT},
        format=lambda: "CONFLICT",
    )
    layer = MagicMock()
    layer.project_root = str(tmp_path)
    layer.state = st
    layer.detect.return_value = detect
    layer.apply_world_to_disk_decision.return_value = SyncReport(
        status=CONFLICT, detail="phase_c:execution_failed: still going"
    )

    tools = make_tools(workspace=MagicMock(), allow_mutation=False, sync_layer=layer)
    result = tools["forge_sync"]()
    assert result.payload.get("decision_status") == "execution_failed"
    loaded = SyncDecisionStore(tmp_path).load()
    assert loaded is not None
    assert loaded.decision_id == old_id
    assert loaded.status == STATUS_DECIDED
    assert loaded.mark_count >= 2


def test_authorization_error_when_receipt_above_generation(tmp_path: Path):
    f = tmp_path / "m.txt"
    f.write_text("v1", encoding="utf-8")
    st = SyncState(tmp_path)
    st._last_known_file_hashes = {str(f): "x"}
    st._disk_synced_version = 5
    st._save()
    report = _report(str(f), world_version=10)
    gen = build_sync_decision_generation(report, st)
    d = SyncDecision.new_pending(CONFLICT, gen)
    d.apply_direction(DIRECTION_WORLD_TO_DISK)

    high = SimpleNamespace(version=99, delta=None, source="forge_tool")
    world = MagicMock()
    world.get_receipts_since.return_value = [high]
    world.get_version.return_value = 10

    layer = SyncLayer(str(tmp_path), world_runtime=world, sync_state=st)
    layer._file_projection = MagicMock()
    out = layer.apply_world_to_disk_decision(d, report)
    assert "authorization_error" in (out.detail or "")
    assert st.disk_synced_version == 5
    assert d.mark_count == 0


def test_per_receipt_mark_advances_watermark(tmp_path: Path):
    f = tmp_path / "m.txt"
    f.write_text("v1", encoding="utf-8")
    st = SyncState(tmp_path)
    st._last_known_file_hashes = {str(f): "x"}
    st._disk_synced_version = 5
    st._save()
    report = _report(str(f), world_version=12)
    gen = build_sync_decision_generation(report, st)
    gen["world_version"] = 12
    d = SyncDecision.new_pending(CONFLICT, gen)
    d.apply_direction(DIRECTION_WORLD_TO_DISK)

    r1 = SimpleNamespace(version=6, delta=None, source="forge_tool")
    r2 = SimpleNamespace(version=8, delta=None, source="forge_tool")
    world = MagicMock()
    world.get_receipts_since.return_value = [r1, r2]
    world.get_version.return_value = 12

    proj = MagicMock()
    proj.apply.return_value = SimpleNamespace(
        success=True, written_paths=[str(f)], deleted_paths=[], reason=""
    )
    proj.prepare.return_value = {
        "files_modified": [str(f)],
        "files_deleted": [],
    }

    layer = SyncLayer(str(tmp_path), world_runtime=world, sync_state=st)
    layer._file_projection = proj
    # detect after will fail world - mock detect at end
    layer.detect = MagicMock(
        return_value=SyncReport(status=IN_SYNC, world_version=12, disk_synced_version=8)
    )

    out = layer.apply_world_to_disk_decision(d, report)
    assert d.mark_count == 2
    assert d.last_marked_version == 8
    assert st.disk_synced_version == 8
    assert st.disk_synced_version <= 12
    assert out.status == IN_SYNC

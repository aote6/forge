"""Phase B: disk_to_world executor (accept_disk_wins + preflight + verify/clear)."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from forge.sync.decision import (
    DIRECTION_DISK_TO_WORLD,
    DIRECTION_WORLD_TO_DISK,
    STATUS_DECIDED,
    STATUS_PENDING,
    SyncDecision,
    SyncDecisionStore,
    build_sync_decision_generation,
)
from forge.sync.state import SyncState
from forge.sync.sync_layer import CONFLICT, IN_SYNC, SyncLayer, SyncReport
from forge.tools import make_tools


def test_accept_disk_wins_rebuilds_hashes_and_sets_watermark(tmp_path: Path):
    f = tmp_path / "a.txt"
    f.write_text("hello", encoding="utf-8")
    st = SyncState(tmp_path)
    st._last_known_file_hashes = {str(f): "old"}
    st._disk_synced_version = 5
    st._save()

    st.accept_disk_wins(118, source="user_reconcile_disk_wins", recompute_hashes=True)
    assert st.disk_synced_version == 118
    assert st.disk_synced_version <= 118
    assert st.last_known_file_hashes[str(f)] != "old"
    assert st.last_sync is not None
    assert st.last_sync.get("source") == "user_reconcile_disk_wins"
    assert st.last_sync.get("version") == 118


def test_accept_disk_wins_drops_missing_known_paths(tmp_path: Path):
    gone = tmp_path / "gone.txt"
    stay = tmp_path / "stay.txt"
    stay.write_text("x", encoding="utf-8")
    st = SyncState(tmp_path)
    st._last_known_file_hashes = {str(gone): "g", str(stay): "s"}
    st._disk_synced_version = 1
    st._save()
    st.accept_disk_wins(3)
    assert str(gone) not in st.last_known_file_hashes
    assert str(stay) in st.last_known_file_hashes


def test_accept_disk_wins_refuses_when_current_watermark_above_authorized(tmp_path: Path):
    st = SyncState(tmp_path)
    st._disk_synced_version = 200
    st._save()
    try:
        st.accept_disk_wins(118)
        assert False, "expected ValueError"
    except ValueError:
        pass
    assert st.disk_synced_version == 200


def test_apply_preflight_stale_no_mutation(tmp_path: Path):
    f = tmp_path / "m.txt"
    f.write_text("v1", encoding="utf-8")
    st = SyncState(tmp_path)
    st._last_known_file_hashes = {str(f): "x"}
    st._disk_synced_version = 5
    st._save()

    report = SimpleNamespace(
        status=CONFLICT,
        conflict_kind="content_divergence",
        world_version=10,
        disk_synced_version=5,
        known_commit="k",
        disk_commit="d",
        divergent_paths=[str(f)],
        detail="c",
    )
    gen = build_sync_decision_generation(report, st)
    d = SyncDecision.new_pending(CONFLICT, gen)
    d.apply_direction(DIRECTION_DISK_TO_WORLD)

    f.write_text("v2", encoding="utf-8")  # fingerprint stale

    layer = SyncLayer(str(tmp_path), world_runtime=MagicMock(), sync_state=st)
    before = st.disk_synced_version
    out = layer.apply_disk_to_world_decision(d, report)
    assert "preflight_stale" in (out.detail or "")
    assert st.disk_synced_version == before


def test_apply_world_version_119_vs_g118_stale_no_mutation(tmp_path: Path):
    f = tmp_path / "m.txt"
    f.write_text("v1", encoding="utf-8")
    st = SyncState(tmp_path)
    st._last_known_file_hashes = {str(f): "x"}
    st._disk_synced_version = 5
    st._save()

    report_g = SimpleNamespace(
        status=CONFLICT,
        conflict_kind="content_divergence",
        world_version=118,
        disk_synced_version=5,
        known_commit="k",
        disk_commit="d",
        divergent_paths=[str(f)],
        detail="c",
    )
    gen = build_sync_decision_generation(report_g, st)
    d = SyncDecision.new_pending(CONFLICT, gen)
    d.apply_direction(DIRECTION_DISK_TO_WORLD)

    report_now = SimpleNamespace(
        status=CONFLICT,
        conflict_kind="content_divergence",
        world_version=119,
        disk_synced_version=5,
        known_commit="k",
        disk_commit="d",
        divergent_paths=[str(f)],
        detail="c",
    )
    layer = SyncLayer(str(tmp_path), world_runtime=MagicMock(), sync_state=st)
    out = layer.apply_disk_to_world_decision(d, report_now)
    assert "preflight_stale" in (out.detail or "")
    assert st.disk_synced_version == 5
    assert st.disk_synced_version != 119
    assert st.disk_synced_version != 118 or st.last_sync is None or st.last_sync.get(
        "source"
    ) != "user_reconcile_disk_wins"


def test_forge_sync_disk_to_world_success_clears_decision(tmp_path: Path, monkeypatch):
    f = tmp_path / "m.txt"
    f.write_text("v1", encoding="utf-8")
    st = SyncState(tmp_path)
    st._last_known_file_hashes = {str(f): "x"}
    st._disk_synced_version = 5
    st._last_known_commit = "d"
    st._save()

    report = SimpleNamespace(
        status=CONFLICT,
        conflict_kind="content_divergence",
        world_version=10,
        disk_synced_version=5,
        known_commit="d",
        disk_commit="d",
        divergent_paths=[str(f)],
        detail="c",
        to_dict=lambda: {"status": CONFLICT},
        format=lambda: "CONFLICT",
    )
    gen = build_sync_decision_generation(report, st)
    d = SyncDecision.new_pending(CONFLICT, gen)
    d.apply_direction(DIRECTION_DISK_TO_WORLD)
    SyncDecisionStore(tmp_path).save(d)

    in_sync = SyncReport(status=IN_SYNC, world_version=10, disk_synced_version=10)

    layer = MagicMock()
    layer.project_root = str(tmp_path)
    layer.state = st
    layer.detect.return_value = report

    def _apply(decision, rep):
        st.accept_disk_wins(int(decision.generation["world_version"]))
        return in_sync

    layer.apply_disk_to_world_decision.side_effect = _apply

    tools = make_tools(workspace=MagicMock(), allow_mutation=False, sync_layer=layer)
    result = tools["forge_sync"]()
    assert result.success is True
    assert result.payload.get("decision_status") == "cleared"
    assert result.payload.get("phase") == "B"
    assert SyncDecisionStore(tmp_path).load() is None
    assert st.disk_synced_version == 10


def test_forge_sync_execution_failed_keeps_decided(tmp_path: Path):
    f = tmp_path / "m.txt"
    f.write_text("v1", encoding="utf-8")
    st = SyncState(tmp_path)
    st._last_known_file_hashes = {str(f): "x"}
    st._disk_synced_version = 5
    st._save()

    report = SimpleNamespace(
        status=CONFLICT,
        conflict_kind="content_divergence",
        world_version=10,
        disk_synced_version=5,
        known_commit="k",
        disk_commit="d",
        divergent_paths=[str(f)],
        detail="c",
        to_dict=lambda: {"status": CONFLICT},
        format=lambda: "CONFLICT",
    )
    gen = build_sync_decision_generation(report, st)
    d = SyncDecision.new_pending(CONFLICT, gen)
    d.apply_direction(DIRECTION_DISK_TO_WORLD)
    SyncDecisionStore(tmp_path).save(d)
    old_id = d.decision_id

    failed = SyncReport(status=CONFLICT, detail="still conflict after accept")
    layer = MagicMock()
    layer.project_root = str(tmp_path)
    layer.state = st
    layer.detect.return_value = report
    layer.apply_disk_to_world_decision.return_value = failed

    tools = make_tools(workspace=MagicMock(), allow_mutation=False, sync_layer=layer)
    result = tools["forge_sync"]()
    assert result.success is False
    assert result.payload.get("decision_status") == "execution_failed"
    loaded = SyncDecisionStore(tmp_path).load()
    assert loaded is not None
    assert loaded.decision_id == old_id
    assert loaded.status == STATUS_DECIDED


def test_forge_sync_world_to_disk_invokes_phase_c_executor(tmp_path: Path):
    """Phase C：applicable + world_to_disk 走 apply_world_to_disk_decision，不调用 sync()。"""
    f = tmp_path / "m.txt"
    f.write_text("v1", encoding="utf-8")
    st = SyncState(tmp_path)
    st._last_known_file_hashes = {str(f): "x"}
    st._disk_synced_version = 5
    st._save()
    report = SimpleNamespace(
        status=CONFLICT,
        conflict_kind="content_divergence",
        world_version=10,
        disk_synced_version=5,
        known_commit="k",
        disk_commit="d",
        divergent_paths=[str(f)],
        detail="c",
        to_dict=lambda: {"status": CONFLICT, "world_version": 10},
        format=lambda: "CONFLICT",
    )
    gen = build_sync_decision_generation(report, st)
    d = SyncDecision.new_pending(CONFLICT, gen)
    d.apply_direction(DIRECTION_WORLD_TO_DISK)
    SyncDecisionStore(tmp_path).save(d)

    in_sync = SyncReport(status=IN_SYNC, world_version=10, disk_synced_version=10)

    layer = MagicMock()
    layer.project_root = str(tmp_path)
    layer.state = st
    layer.detect.return_value = report
    layer.apply_disk_to_world_decision = MagicMock(
        side_effect=AssertionError("must not run disk_to_world path")
    )
    layer.apply_world_to_disk_decision = MagicMock(return_value=in_sync)
    layer.sync = MagicMock(side_effect=AssertionError("must not sync"))

    tools = make_tools(workspace=MagicMock(), allow_mutation=False, sync_layer=layer)
    result = tools["forge_sync"]()
    assert result.success is True or getattr(result, "ok", None) is True
    layer.apply_world_to_disk_decision.assert_called_once()
    layer.sync.assert_not_called()
    assert result.payload.get("decision_status") == "cleared"
    assert result.payload.get("phase") == "C"

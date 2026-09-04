"""Phase A: SyncDecision generation / applicability / forge_sync protocol."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

from forge.adapters.base import ToolResult
from forge.sync.decision import (
    ALREADY_IN_SYNC,
    APPLICABLE,
    DIRECTION_DISK_TO_WORLD,
    LEGACY_NO_GENERATION,
    STATUS_DECIDED,
    STATUS_PENDING,
    STALE,
    SyncDecision,
    SyncDecisionStore,
    build_sync_decision_generation,
    classify_decision_applicability,
    fingerprint_managed_disk,
    supersede_decided_with_pending,
)
from forge.sync.sync_layer import CONFLICT, IN_SYNC
from forge.tools import make_tools


def _report(**kwargs):
    base = dict(
        status=CONFLICT,
        conflict_kind="content_divergence",
        world_version=10,
        disk_synced_version=5,
        known_commit="aaa",
        disk_commit="bbb",
        divergent_paths=[],
        detail="test conflict",
    )
    base.update(kwargs)
    return SimpleNamespace(**base)


def _state(hashes=None, dsv=5):
    st = SimpleNamespace()
    st.last_known_file_hashes = dict(hashes or {})
    st.disk_synced_version = dsv
    return st


def test_fingerprint_stable_and_path_order_independent(tmp_path: Path):
    f1 = tmp_path / "a.txt"
    f2 = tmp_path / "b.txt"
    f1.write_text("one", encoding="utf-8")
    f2.write_text("two", encoding="utf-8")
    fp1 = fingerprint_managed_disk([str(f1), str(f2)])
    fp2 = fingerprint_managed_disk([str(f2), str(f1)])
    assert fp1 == fp2
    assert len(fp1) == 64


def test_build_generation_includes_required_keys(tmp_path: Path):
    f = tmp_path / "m.txt"
    f.write_text("x", encoding="utf-8")
    report = _report(divergent_paths=[str(f)])
    st = _state({str(f): "deadbeef"})
    gen = build_sync_decision_generation(report, st)
    for key in (
        "basis",
        "conflict_kind",
        "world_version",
        "disk_synced_version",
        "known_commit",
        "disk_commit",
        "divergent_paths",
        "path_hash_fingerprint",
    ):
        assert key in gen
    assert gen["basis"] == CONFLICT
    assert gen["path_hash_fingerprint"]


def test_round_trip_with_generation(tmp_path: Path):
    f = tmp_path / "m.txt"
    f.write_text("x", encoding="utf-8")
    gen = build_sync_decision_generation(
        _report(divergent_paths=[str(f)]), _state({str(f): "h"})
    )
    d = SyncDecision.new_pending(basis=CONFLICT, generation=gen)
    store = SyncDecisionStore(tmp_path)
    store.save(d)
    loaded = store.load()
    assert loaded is not None
    assert loaded.generation is not None
    assert loaded.generation["path_hash_fingerprint"] == gen["path_hash_fingerprint"]


def test_from_dict_legacy_without_generation():
    d = SyncDecision.from_dict(
        {
            "decision_id": "sd_legacy",
            "basis": CONFLICT,
            "direction": DIRECTION_DISK_TO_WORLD,
            "status": STATUS_DECIDED,
            "created_at": 1.0,
            "decided_at": 2.0,
        }
    )
    assert d is not None
    assert d.generation is None
    assert d.status == STATUS_DECIDED


def test_classify_legacy_no_generation(tmp_path: Path):
    d = SyncDecision.from_dict(
        {
            "decision_id": "sd_legacy",
            "basis": CONFLICT,
            "direction": DIRECTION_DISK_TO_WORLD,
            "status": STATUS_DECIDED,
            "created_at": 1.0,
            "decided_at": 2.0,
        }
    )
    report = _report()
    assert (
        classify_decision_applicability(d, report, _state()) == LEGACY_NO_GENERATION
    )


def test_classify_applicable_and_stale_on_hash_change(tmp_path: Path):
    f = tmp_path / "m.txt"
    f.write_text("v1", encoding="utf-8")
    st = _state({str(f): "ignored"})
    report = _report(
        divergent_paths=[str(f)],
        world_version=10,
        disk_synced_version=5,
        known_commit="k",
        disk_commit="d",
    )
    gen = build_sync_decision_generation(report, st)
    d = SyncDecision.new_pending(basis=CONFLICT, generation=gen)
    d.apply_direction(DIRECTION_DISK_TO_WORLD)
    assert classify_decision_applicability(d, report, st) == APPLICABLE

    f.write_text("v2-changed", encoding="utf-8")
    assert classify_decision_applicability(d, report, st) == STALE


def test_classify_stale_on_world_version_and_disk_commit(tmp_path: Path):
    f = tmp_path / "m.txt"
    f.write_text("v1", encoding="utf-8")
    st = _state({str(f): "x"})
    report = _report(divergent_paths=[str(f)], world_version=10, disk_commit="c1")
    gen = build_sync_decision_generation(report, st)
    d = SyncDecision.new_pending(basis=CONFLICT, generation=gen)
    d.apply_direction(DIRECTION_DISK_TO_WORLD)

    r2 = _report(divergent_paths=[str(f)], world_version=11, disk_commit="c1")
    assert classify_decision_applicability(d, r2, st) == STALE

    r3 = _report(divergent_paths=[str(f)], world_version=10, disk_commit="c2")
    # fingerprint may also change if paths same content — disk_commit alone must stale
    assert classify_decision_applicability(d, r3, st) == STALE


def test_classify_already_in_sync(tmp_path: Path):
    d = SyncDecision.new_pending(basis=CONFLICT, generation={"basis": CONFLICT})
    d.apply_direction(DIRECTION_DISK_TO_WORLD)
    report = _report(status=IN_SYNC)
    assert classify_decision_applicability(d, report, _state()) == ALREADY_IN_SYNC


def test_apply_direction_preserves_generation(tmp_path: Path):
    gen = {"basis": CONFLICT, "path_hash_fingerprint": "abc", "divergent_paths": []}
    # incomplete gen ok for this unit test of preservation
    d = SyncDecision.new_pending(basis=CONFLICT, generation=dict(gen))
    d.apply_direction(DIRECTION_DISK_TO_WORLD)
    assert d.generation == gen
    assert d.status == STATUS_DECIDED


def test_supersede_atomic_replaces_decided(tmp_path: Path):
    f = tmp_path / "m.txt"
    f.write_text("v1", encoding="utf-8")
    st = _state({str(f): "x"})
    report = _report(divergent_paths=[str(f)])
    gen = build_sync_decision_generation(report, st)
    old = SyncDecision.new_pending(basis=CONFLICT, generation=gen)
    old.apply_direction(DIRECTION_DISK_TO_WORLD)
    store = SyncDecisionStore(tmp_path)
    store.save(old)
    old_id = old.decision_id

    new_d = supersede_decided_with_pending(tmp_path, report, st)
    assert new_d.decision_id != old_id
    assert new_d.status == STATUS_PENDING
    assert new_d.generation is not None
    loaded = store.load()
    assert loaded is not None
    assert loaded.decision_id == new_d.decision_id
    assert loaded.status == STATUS_PENDING


def test_forge_sync_authorized_pending_no_syncstate_writes(tmp_path: Path):
    """resolve 后 applicable + disk_to_world → 调用 executor，不调用 sync() 或旧 SyncState 写入。"""
    f = tmp_path / "m.txt"
    f.write_text("v1", encoding="utf-8")
    st = _state({str(f): "x"}, dsv=5)
    report = _report(
        divergent_paths=[str(f)],
        world_version=10,
        disk_synced_version=5,
        known_commit="k",
        disk_commit="d",
    )
    gen = build_sync_decision_generation(report, st)
    d = SyncDecision.new_pending(basis=CONFLICT, generation=gen)
    d.apply_direction(DIRECTION_DISK_TO_WORLD)
    SyncDecisionStore(tmp_path).save(d)

    in_sync = SimpleNamespace(
        status=IN_SYNC,
        world_version=10,
        disk_synced_version=10,
        known_commit="k",
        disk_commit="d",
        divergent_paths=[],
        detail="",
        to_dict=lambda: {"status": IN_SYNC},
        format=lambda: "sync_status: IN_SYNC",
    )

    sync_layer = MagicMock()
    sync_layer.project_root = str(tmp_path)
    sync_layer.state = st
    sync_layer.detect.return_value = report
    sync_layer.apply_disk_to_world_decision.return_value = in_sync
    sync_layer.sync = MagicMock(
        side_effect=AssertionError("sync() must not run when applicable")
    )
    st.record_external_sync = MagicMock(
        side_effect=AssertionError("record_external_sync forbidden")
    )
    st.mark_disk_synced = MagicMock(
        side_effect=AssertionError("mark_disk_synced forbidden")
    )

    tools = make_tools(
        workspace=MagicMock(),
        allow_mutation=False,
        sync_layer=sync_layer,
    )
    result = tools["forge_sync"]()
    assert result.success is True
    assert result.payload.get("decision_status") == "cleared"
    assert result.payload.get("direction") == DIRECTION_DISK_TO_WORLD
    sync_layer.sync.assert_not_called()
    sync_layer.apply_disk_to_world_decision.assert_called_once()

def test_forge_sync_stale_opens_new_pending(tmp_path: Path):
    f = tmp_path / "m.txt"
    f.write_text("v1", encoding="utf-8")
    st = _state({str(f): "x"})
    report_ns = _report(
        divergent_paths=[str(f)],
        world_version=10,
        disk_synced_version=5,
        known_commit="k",
        disk_commit="d",
    )
    gen = build_sync_decision_generation(report_ns, st)
    d = SyncDecision.new_pending(basis=CONFLICT, generation=gen)
    d.apply_direction(DIRECTION_DISK_TO_WORLD)
    SyncDecisionStore(tmp_path).save(d)
    old_id = d.decision_id

    # change content → fingerprint stale
    f.write_text("v2", encoding="utf-8")

    def _fmt():
        return "sync_status: CONFLICT"

    def _to_dict():
        return {
            "status": CONFLICT,
            "world_version": 10,
            "disk_synced_version": 5,
            "known_commit": "k",
            "disk_commit": "d",
            "divergent_paths": [str(f)],
            "conflict_kind": "content_divergence",
            "detail": "conflict",
            "world_advanced": True,
            "disk_advanced": True,
            "diff_hint": "",
        }

    detect_report = SimpleNamespace(
        status=CONFLICT,
        conflict_kind="content_divergence",
        world_version=10,
        disk_synced_version=5,
        known_commit="k",
        disk_commit="d",
        divergent_paths=[str(f)],
        detail="conflict",
        to_dict=_to_dict,
        format=_fmt,
    )
    sync_layer = MagicMock()
    sync_layer.project_root = str(tmp_path)
    sync_layer.state = st
    sync_layer.detect.return_value = detect_report
    sync_layer.sync = MagicMock(
        side_effect=AssertionError("sync must not run on stale path")
    )

    tools = make_tools(
        workspace=MagicMock(), allow_mutation=False, sync_layer=sync_layer
    )
    result = tools["forge_sync"]()
    assert result.success is True
    assert result.payload.get("decision_status") == "stale"
    assert result.payload.get("pending_opened") is True
    assert result.payload.get("old_decision_id") == old_id
    new_id = result.payload.get("new_decision_id")
    assert new_id and new_id != old_id
    loaded = SyncDecisionStore(tmp_path).load()
    assert loaded is not None
    assert loaded.decision_id == new_id
    assert loaded.status == STATUS_PENDING


def test_forge_sync_legacy_decided_treated_as_stale(tmp_path: Path):
    store = SyncDecisionStore(tmp_path)
    legacy = SyncDecision.from_dict(
        {
            "decision_id": "sd_oldformat",
            "basis": CONFLICT,
            "direction": DIRECTION_DISK_TO_WORLD,
            "status": STATUS_DECIDED,
            "created_at": 1.0,
            "decided_at": 2.0,
        }
    )
    assert legacy is not None
    store.save(legacy)

    def _fmt():
        return "sync_status: CONFLICT"

    def _to_dict():
        return {
            "status": CONFLICT,
            "world_version": 1,
            "disk_synced_version": 0,
            "known_commit": "",
            "disk_commit": "",
            "divergent_paths": [],
            "conflict_kind": "content_divergence",
            "detail": "",
            "world_advanced": True,
            "disk_advanced": True,
            "diff_hint": "",
        }

    report = SimpleNamespace(
        status=CONFLICT,
        conflict_kind="content_divergence",
        world_version=1,
        disk_synced_version=0,
        known_commit="",
        disk_commit="",
        divergent_paths=[],
        detail="",
        to_dict=_to_dict,
        format=_fmt,
    )
    sync_layer = MagicMock()
    sync_layer.project_root = str(tmp_path)
    sync_layer.state = _state()
    sync_layer.detect.return_value = report
    sync_layer.sync = MagicMock(
        side_effect=AssertionError("must not execute on legacy DECIDED")
    )

    tools = make_tools(
        workspace=MagicMock(), allow_mutation=False, sync_layer=sync_layer
    )
    result = tools["forge_sync"]()
    assert result.success is True
    assert result.payload.get("decision_status") == "stale"
    assert result.payload.get("reason") == LEGACY_NO_GENERATION
    loaded = SyncDecisionStore(tmp_path).load()
    assert loaded is not None
    assert loaded.status == STATUS_PENDING
    assert loaded.decision_id != "sd_oldformat"


def _make_conflict_report(path: str, *, world_version=10, disk_commit="d", divergent=None):
    div = list(divergent) if divergent is not None else [path]

    def _to_dict():
        return {
            "status": CONFLICT,
            "world_version": world_version,
            "disk_synced_version": 5,
            "known_commit": "k",
            "disk_commit": disk_commit,
            "divergent_paths": list(div),
            "conflict_kind": "content_divergence",
            "detail": "conflict",
            "world_advanced": True,
            "disk_advanced": True,
            "diff_hint": "",
        }

    return SimpleNamespace(
        status=CONFLICT,
        conflict_kind="content_divergence",
        world_version=world_version,
        disk_synced_version=5,
        known_commit="k",
        disk_commit=disk_commit,
        divergent_paths=list(div),
        detail="conflict",
        to_dict=_to_dict,
        format=lambda: "sync_status: CONFLICT",
    )


def test_forge_sync_stale_when_non_divergent_managed_file_changes(tmp_path: Path):
    """managed 文件不在 divergent_paths，但仍在 last_known_file_hashes → 改内容须 stale。"""
    tracked = tmp_path / "tracked.txt"
    other = tmp_path / "other_managed.txt"
    tracked.write_text("t1", encoding="utf-8")
    other.write_text("o1", encoding="utf-8")

    st = _state({str(tracked): "h1", str(other): "h2"}, dsv=5)
    # divergent only lists tracked; other is managed via known hashes only
    report0 = _report(
        divergent_paths=[str(tracked)],
        world_version=10,
        disk_synced_version=5,
        known_commit="k",
        disk_commit="d",
    )
    gen = build_sync_decision_generation(report0, st)
    d = SyncDecision.new_pending(basis=CONFLICT, generation=gen)
    d.apply_direction(DIRECTION_DISK_TO_WORLD)
    SyncDecisionStore(tmp_path).save(d)
    old_id = d.decision_id

    other.write_text("o2-changed", encoding="utf-8")

    detect_report = _make_conflict_report(
        str(tracked), divergent=[str(tracked)]
    )
    sync_layer = MagicMock()
    sync_layer.project_root = str(tmp_path)
    sync_layer.state = st
    sync_layer.detect.return_value = detect_report
    sync_layer.sync = MagicMock(
        side_effect=AssertionError("sync must not run on stale")
    )

    tools = make_tools(
        workspace=MagicMock(), allow_mutation=False, sync_layer=sync_layer
    )
    result = tools["forge_sync"]()
    assert result.success is True
    assert result.payload.get("decision_status") == "stale"
    assert result.payload.get("pending_opened") is True
    assert result.payload.get("old_decision_id") == old_id
    loaded = SyncDecisionStore(tmp_path).load()
    assert loaded is not None and loaded.status == STATUS_PENDING
    assert loaded.decision_id != old_id


def test_forge_sync_stale_when_disk_commit_changes_hashes_same(tmp_path: Path):
    """disk_commit 变、managed 内容 hash 不变 → 仍须 stale。"""
    f = tmp_path / "m.txt"
    f.write_text("same-bytes", encoding="utf-8")
    st = _state({str(f): "x"}, dsv=5)
    report0 = _report(
        divergent_paths=[str(f)],
        world_version=10,
        disk_synced_version=5,
        known_commit="k",
        disk_commit="commit_a",
    )
    gen = build_sync_decision_generation(report0, st)
    d = SyncDecision.new_pending(basis=CONFLICT, generation=gen)
    d.apply_direction(DIRECTION_DISK_TO_WORLD)
    SyncDecisionStore(tmp_path).save(d)
    old_id = d.decision_id

    # content unchanged; only disk_commit differs in fresh detect report
    detect_report = _make_conflict_report(
        str(f), world_version=10, disk_commit="commit_b", divergent=[str(f)]
    )
    sync_layer = MagicMock()
    sync_layer.project_root = str(tmp_path)
    sync_layer.state = st
    sync_layer.detect.return_value = detect_report
    sync_layer.sync = MagicMock(
        side_effect=AssertionError("sync must not run on stale")
    )

    tools = make_tools(
        workspace=MagicMock(), allow_mutation=False, sync_layer=sync_layer
    )
    result = tools["forge_sync"]()
    assert result.success is True
    assert result.payload.get("decision_status") == "stale"
    assert result.payload.get("pending_opened") is True
    assert result.payload.get("old_decision_id") == old_id
    loaded = SyncDecisionStore(tmp_path).load()
    assert loaded is not None and loaded.status == STATUS_PENDING
    assert loaded.decision_id != old_id


def test_supersede_blocked_when_human_intervention_pending(tmp_path: Path):
    """HI pending 时不得 save 覆盖 SyncDecision（检查在 save 之前）。"""
    from forge.runtime_state import (
        PHASE_AWAITING_USER,
        PENDING_KIND_HUMAN_INTERVENTION,
        Pending,
        RuntimeState,
        RuntimeStateStore,
    )

    f = tmp_path / "m.txt"
    f.write_text("v1", encoding="utf-8")
    st = _state({str(f): "x"})
    report = _report(divergent_paths=[str(f)])
    gen = build_sync_decision_generation(report, st)
    old = SyncDecision.new_pending(basis=CONFLICT, generation=gen)
    old.apply_direction(DIRECTION_DISK_TO_WORLD)
    store = SyncDecisionStore(tmp_path)
    store.save(old)
    old_id = old.decision_id
    old_raw = store.path.read_text(encoding="utf-8")

    RuntimeStateStore(tmp_path).save(
        RuntimeState(
            phase=PHASE_AWAITING_USER,
            pending=Pending(
                kind=PENDING_KIND_HUMAN_INTERVENTION,
                summary="hi",
                payload={"reason": "test"},
            ),
        )
    )

    out = supersede_decided_with_pending(tmp_path, report, st)
    assert out is None
    # SyncDecision file must be unchanged
    assert store.path.read_text(encoding="utf-8") == old_raw
    loaded = store.load()
    assert loaded is not None
    assert loaded.decision_id == old_id
    assert loaded.status == STATUS_DECIDED

    # forge_sync path under HI: stale report but no overwrite
    detect_report = _make_conflict_report(str(f), divergent=[str(f)])
    # force stale by changing content while HI holds slot
    f.write_text("v2", encoding="utf-8")
    sync_layer = MagicMock()
    sync_layer.project_root = str(tmp_path)
    sync_layer.state = st
    sync_layer.detect.return_value = detect_report
    sync_layer.sync = MagicMock(
        side_effect=AssertionError("sync must not run")
    )
    tools = make_tools(
        workspace=MagicMock(), allow_mutation=False, sync_layer=sync_layer
    )
    result = tools["forge_sync"]()
    assert result.success is True
    assert result.payload.get("decision_status") == "stale"
    assert result.payload.get("pending_opened") is False
    assert result.payload.get("blocked_by") == "human_intervention"
    loaded2 = store.load()
    assert loaded2 is not None and loaded2.decision_id == old_id

"""P0-4 batch2: surface projection/runtime consistency failures (no silent swallow)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from forge.projections.base import ProjectionResult, TransactionDelta
from forge.projections.file_projection import FileProjection
from forge.sync.state import SyncState
from forge.world.types import Receipt


def _receipt(version: int = 1) -> Receipt:
    return Receipt(tx_id=1, version=version, before_root=0, after_root=1, source="test")


def _delta_write(path: str, content: str) -> TransactionDelta:
    return TransactionDelta(
        memory_written=[
            {"object_id": 1001, "state_id": 0, "value_hex": path.encode("utf-8").hex()},
            {"object_id": 1001, "state_id": 1, "value_hex": content.encode("utf-8").hex()},
        ],
    )


# ── Case A: forget_paths failure during rollback ─────────────────

def test_forget_paths_failure_surfaces_in_projection_result(tmp_path):
    """rollback 产生 uncertain 后 forget_paths 抛错 → apply 返回 success=False，可观察。"""
    root = tmp_path
    target = root / "a.txt"
    target.write_text("v1\n", encoding="utf-8")

    state = SyncState(project_root=str(root))
    state._last_known_file_hashes[str(target)] = "deadbeef"
    state._save()

    fp = FileProjection(project_root=str(root), sync_state=state)
    delta = _delta_write(str(target), "v2\n")

    def _boom_forget(paths):
        raise OSError("sync state disk full")

    # 写入成功使 applied 非空，校验失败触发 rollback；
    # restore 失败 → uncertain；forget_paths 再失败 → 必须出现在 result.reason。
    with patch.object(fp.backup, "restore_latest", return_value=False), \
         patch.object(state, "forget_paths", side_effect=_boom_forget), \
         patch(
             "forge.projections.file_projection.ValidatorRegistry.validate",
             return_value=(False, "inject validate fail"),
         ):
        result = fp.apply(_receipt(), delta)

    assert isinstance(result, ProjectionResult)
    assert result.success is False
    assert "forget_paths" in (result.reason or "")
    assert result.uncertain_paths or "uncertain" in (result.reason or "").lower()


def test_forget_paths_success_still_removes_hashes(tmp_path):
    """正常 forget_paths 仍应从 known hashes 移除 uncertain 路径。"""
    root = tmp_path
    target = root / "b.txt"
    target.write_text("x\n", encoding="utf-8")
    state = SyncState(project_root=str(root))
    state._last_known_file_hashes[str(target)] = "abc"
    state._save()

    fp = FileProjection(project_root=str(root), sync_state=state)
    uncertain = fp._rollback_applied([str(target)])
    assert str(target) in uncertain
    assert str(target) not in state.last_known_file_hashes


# ── Case B: ensure_identity failure aborts Runtime init ───────────

def test_ensure_identity_failure_aborts_runtime_init(tmp_path):
    """world.ensure_identity 失败时 Runtime 不得以正常状态完成初始化。"""
    from forge.runtime import Runtime
    from forge.workspace import Workspace
    from forge.memory import MemoryStore

    workspace = Workspace(project_root=str(tmp_path))
    memory = MemoryStore()
    adapter = MagicMock()

    with patch("forge.runtime.WorldRuntime") as WR:
        world = MagicMock()
        world.ensure_identity.side_effect = RuntimeError("veritasd down")
        world._path_map = None
        WR.return_value = world

        with pytest.raises(RuntimeError) as ei:
            Runtime(adapter, workspace, memory)

    assert "identity" in str(ei.value).lower() or "Identity" in str(ei.value)
    world.ensure_identity.assert_called_once()


# ── Case C: path_map rebuild failure is observable ────────────────

def test_path_map_rebuild_failure_sets_degraded_flag():
    """_rebuild_path_map 中 update_from_delta 失败 → 不再静默，标记 degraded。"""
    from forge.world.runtime import WorldRuntime
    from forge.projections.object_path import ObjectPathMap

    # 构造绕过真实 adapter 的实例
    rt = object.__new__(WorldRuntime)
    rt.project_root = "/tmp"
    rt._adapter = MagicMock()
    rt._path_map = ObjectPathMap()

    bad_delta = MagicMock()
    bad_delta.memory_written = [{"object_id": 1, "state_id": 0, "value_hex": "not-hex!!!"}]
    # force update_from_delta to raise
    with patch.object(ObjectPathMap, "update_from_delta", side_effect=RuntimeError("boom")):
        receipt = MagicMock()
        receipt.delta = bad_delta
        receipt.version = 9
        with patch.object(rt, "get_receipts_since", return_value=[receipt]):
            # re-import path for the method's local import
            rt._rebuild_path_map()

    assert getattr(rt, "_path_map_degraded", False) is True

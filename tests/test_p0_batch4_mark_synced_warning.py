"""P0-4 batch4: mark_disk_synced failure after a successful write must be observable.

磁盘已写成功 → success=True；但 sync_state 水位未推进 → 必须挂告警，不得静默。
这些测试走真实生产入口（FileProjection.apply / make_intent_tools 工具链），
而不是直接调 helper。
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from forge.projections.base import ProjectionManager, ProjectionResult
from forge.projections.file_projection import FileProjection
from forge.sync.state import SyncState
from forge.tools.intent_tools import make_intent_tools
from forge.world.types import Receipt, TransactionDelta


def _receipt(version: int = 1) -> Receipt:
    return Receipt(tx_id=1, before_root=0, after_root=1, version=version, source="forge_tool")


def _delta_write(path: str, content: str, oid: int = 1001) -> TransactionDelta:
    return TransactionDelta(
        objects_created=[oid],
        memory_written=[
            {"object_id": oid, "state_id": 0, "value_hex": path.encode("utf-8").hex()},
            {"object_id": oid, "state_id": 1, "value_hex": content.encode("utf-8").hex()},
        ],
    )


def test_apply_surfaces_mark_disk_synced_failure_as_warning(tmp_path):
    """FileProjection.apply：写盘成功但 mark_disk_synced 抛 → success=True + warning。"""
    root = tmp_path
    target = root / "f.txt"
    sync_state = SyncState(project_root=str(root))
    fp = FileProjection(project_root=str(root), object_path_map=None, sync_state=sync_state)

    result = None
    with patch.object(
        sync_state, "mark_disk_synced", side_effect=OSError("sync_state unwritable")
    ):
        result = fp.apply(_receipt(1), _delta_write(str(target), "new\n"))

    assert isinstance(result, ProjectionResult)
    assert result.success is True
    assert result.warning and "mark_disk_synced" in result.warning
    assert target.read_text(encoding="utf-8") == "new\n"


def _build_tools(tmp_path):
    """真实 ProjectionManager + FileProjection + SyncState，配一个假 executor。"""
    root = tmp_path
    sync_state = SyncState(project_root=str(root))
    fp = FileProjection(project_root=str(root), object_path_map=None, sync_state=sync_state)
    pm = ProjectionManager(checkpoint_dir=str(root / ".forge"))
    pm.register(fp)

    world = SimpleNamespace(project_root=str(root), _path_map=None)
    executor = MagicMock()
    executor._world = world

    tools = make_intent_tools(executor, pm)
    return root, sync_state, executor, tools


def test_create_file_surfaces_mark_synced_warning(tmp_path):
    """create_file 工具入口：mark_disk_synced 失败 → success=True + payload + display。"""
    root, sync_state, executor, tools = _build_tools(tmp_path)
    path = "a.txt"
    content = "hello\n"
    delta = _delta_write(str(root / path), content)
    executor.execute.return_value = (_receipt(1), delta)

    with patch.object(
        sync_state, "mark_disk_synced", side_effect=OSError("sync_state disk full")
    ):
        result = tools["create_file"](path=path, content=content)

    assert result.success is True
    assert (root / path).read_text(encoding="utf-8") == content
    assert "side_effect_warnings" in (result.payload or {})
    assert any("mark_disk_synced" in w for w in result.payload["side_effect_warnings"])
    assert "SIDE_EFFECT_WARN" in (result.display or "")


def test_write_file_autoregister_surfaces_mark_synced_warning(tmp_path):
    """write_file 新建文件（auto-register 路径）：告警经 delta.metadata 传回。"""
    root, sync_state, executor, tools = _build_tools(tmp_path)
    path = "b.txt"
    content = "hi\n"
    delta = _delta_write(str(root / path), content)
    executor.execute.return_value = (_receipt(2), delta)

    with patch.object(
        sync_state, "mark_disk_synced", side_effect=OSError("quota")
    ):
        result = tools["write_file"](path=path, content=content)

    assert result.success is True
    assert (root / path).read_text(encoding="utf-8") == content
    assert "side_effect_warnings" in (result.payload or {})
    assert any("mark_disk_synced" in w for w in result.payload["side_effect_warnings"])
    assert "SIDE_EFFECT_WARN" in (result.display or "")

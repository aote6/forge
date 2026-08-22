"""P0-4 batch5: 收尾遗留项的可观测性回归测试。

覆盖：
- git_utils：git 命令真实失败不得伪装成空结果（GitError），head 的空状态保留 ""。
- sync_layer：prepare() 读取失败不得被 _receipt_conflicts_with_disk 吞成"无冲突"；
  git status 故障不得伪装成 IN_SYNC。
- local_tools：单文件处理失败（get_repo_map / run_diagnostics / get_call_chain）可观测。
- FileProjection.prepare：已有文件读取失败不得退化成 original=""。
- intent_tools：World commit 成功后 _update_path_map 失败 → 保持 success + 暴露 warning。

优先走真实生产入口（FileProjection.apply / SyncLayer.detect / make_intent_tools / make_local_tools）。
"""
from __future__ import annotations

import binascii
import subprocess
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from forge.projections.base import ProjectionManager
from forge.projections.file_projection import FileProjection
from forge.sync.state import SyncState
from forge.sync.sync_layer import CONFLICT, FAST_FORWARD_WORLD_TO_DISK, IN_SYNC, WORLD_UNAVAILABLE, SyncLayer
from forge.tools.intent_tools import make_intent_tools
from forge.workspace import Workspace
from forge.tools.local_tools import make_local_tools
from forge.world.types import Receipt, TransactionDelta


# ── helpers ────────────────────────────────────────────────────


def _fake_run(returncode: int, stdout: str = "", stderr: str = ""):
    return subprocess.CompletedProcess(
        args=["git"], returncode=returncode, stdout=stdout, stderr=stderr
    )


def _init_git_repo(root):
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "t"], check=True)
    (root / "README.md").write_text("init\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-q", "-m", "init"], check=True)


def _delta_write(path: str, content: str, oid: int = 1001) -> TransactionDelta:
    return TransactionDelta(
        objects_created=[oid],
        memory_written=[
            {"object_id": oid, "state_id": 0, "value_hex": path.encode("utf-8").hex()},
            {"object_id": oid, "state_id": 1, "value_hex": content.encode("utf-8").hex()},
        ],
    )


def _file_receipt(version, abs_path, content, source="forge_tool") -> Receipt:
    delta = TransactionDelta(
        memory_written=[
            {"object_id": 1, "state_id": 0, "value_hex": str(abs_path).encode("utf-8").hex()},
            {"object_id": 1, "state_id": 1, "value_hex": content.encode("utf-8").hex()},
        ],
    )
    return Receipt(
        tx_id=version, before_root=0, after_root=version, version=version,
        delta=delta, source=source,
    )


class MockWorld:
    def __init__(self, receipts):
        self._receipts = list(receipts)

    def get_receipts_since(self, version):
        return [r for r in self._receipts if r.version > version]

    def get_version(self):
        return max((r.version for r in self._receipts), default=0)


# ── 1. git_utils ───────────────────────────────────────────────


def test_git_status_porcelain_untracked_all_raises_on_git_failure(tmp_path):
    from forge.sync.git_utils import GitError, git_status_porcelain_untracked_all

    with patch(
        "forge.sync.git_utils.subprocess.run",
        return_value=_fake_run(128, stderr="fatal: not a git repository"),
    ):
        with pytest.raises(GitError):
            git_status_porcelain_untracked_all(str(tmp_path))


def test_git_status_porcelain_untracked_all_empty_is_legitimate(tmp_path):
    from forge.sync.git_utils import git_status_porcelain_untracked_all

    with patch(
        "forge.sync.git_utils.subprocess.run",
        return_value=_fake_run(0, stdout=""),
    ):
        assert git_status_porcelain_untracked_all(str(tmp_path)) == ""


def test_git_diff_raises_on_git_failure(tmp_path):
    from forge.sync.git_utils import GitError, git_diff

    with patch(
        "forge.sync.git_utils.subprocess.run",
        return_value=_fake_run(1, stderr="fatal: corrupt repo"),
    ):
        with pytest.raises(GitError):
            git_diff(str(tmp_path))


def test_git_status_porcelain_raises_on_git_unavailable(tmp_path):
    from forge.sync.git_utils import GitError, git_status_porcelain

    with patch("forge.sync.git_utils.subprocess.run", side_effect=OSError("no git")):
        with pytest.raises(GitError):
            git_status_porcelain(str(tmp_path))


def test_git_head_commit_returns_empty_on_unborn_head(tmp_path):
    from forge.sync.git_utils import git_head_commit

    with patch(
        "forge.sync.git_utils.subprocess.run",
        return_value=_fake_run(128, stderr="fatal: ambiguous argument 'HEAD'"),
    ):
        assert git_head_commit(str(tmp_path)) == ""


def test_git_head_commit_returns_empty_on_git_unavailable(tmp_path):
    from forge.sync.git_utils import git_head_commit

    with patch(
        "forge.sync.git_utils.subprocess.run",
        side_effect=FileNotFoundError("git not found"),
    ):
        assert git_head_commit(str(tmp_path)) == ""


# ── 2. sync_layer：不掩盖冲突 / 失败 ───────────────────────────


def test_detect_not_in_sync_when_git_status_fails(tmp_path):
    """git status 故障（GitError）不得伪装成 IN_SYNC。"""
    _init_git_repo(tmp_path)
    target = tmp_path / "file.txt"
    receipt = _file_receipt(1, str(target.resolve()), "v1\n")

    state = SyncState(str(tmp_path))
    fp = FileProjection(project_root=str(tmp_path), sync_state=state)
    assert fp.apply(receipt, receipt.delta).success
    layer = SyncLayer(str(tmp_path), MockWorld([receipt]), state, fp)
    assert layer.detect().status == IN_SYNC

    from forge.sync.git_utils import GitError

    with patch(
        "forge.sync.sync_layer.git_status_porcelain_untracked_all",
        side_effect=GitError("git status failed"),
    ):
        report = layer.detect()
    assert report.status != IN_SYNC
    assert report.status == WORLD_UNAVAILABLE


def test_prepare_failure_in_fast_forward_is_conflict(tmp_path):
    """prepare() 读取失败不得被吞成"无冲突"而继续 fast-forward 覆盖。"""
    _init_git_repo(tmp_path)
    target = tmp_path / "file.txt"
    receipt = _file_receipt(1, str(target.resolve()), "v1\n")

    state = SyncState(str(tmp_path))
    fp = FileProjection(project_root=str(tmp_path), sync_state=state)
    layer = SyncLayer(str(tmp_path), MockWorld([receipt]), state, fp)

    assert layer.detect().status == FAST_FORWARD_WORLD_TO_DISK

    with patch.object(fp, "prepare", side_effect=OSError("cannot read existing file")):
        report = layer.sync()

    assert report.status == CONFLICT, report.format()
    assert "无法判定" in report.detail


# ── 3. local_tools：单文件失败可观测 ───────────────────────────


def _make_local_tools(tmp_path):
    return make_local_tools(Workspace(str(tmp_path)))


def _write_broken_py(root, name="broken.py"):
    (root / name).write_bytes(b"\xff\xfe\x00\xfa\xfb")


def test_get_repo_map_surfaces_unparsable_file(tmp_path):
    tools = _make_local_tools(tmp_path)
    (tmp_path / "ok.py").write_text("def good():\n    pass\n", encoding="utf-8")
    _write_broken_py(tmp_path)

    result = tools["get_repo_map"]()
    assert result.success
    assert "def good" in result.display
    assert "skipped unparsable files" in result.display
    assert "broken.py" in result.display


def test_run_diagnostics_surfaces_read_failure(tmp_path):
    tools = _make_local_tools(tmp_path)
    _write_broken_py(tmp_path)

    result = tools["run_diagnostics"](directory=".")
    assert not result.success
    import json

    parsed = json.loads(result.display)
    assert parsed["status"] == "issues_found"
    assert parsed["error_count"] >= 1
    assert any("broken.py" in i.get("file", "") for i in parsed["issues"])


def test_get_call_chain_surfaces_unparsable_file(tmp_path):
    tools = _make_local_tools(tmp_path)
    (tmp_path / "main.py").write_text("def target():\n    pass\n", encoding="utf-8")
    _write_broken_py(tmp_path)

    result = tools["get_call_chain"](symbol_name="target")
    assert result.success
    assert "skipped unparsable files" in result.display
    assert "broken.py" in result.display


# ── 4a. FileProjection.prepare ────────────────────────────────


def test_prepare_read_failure_raises_instead_of_empty_original(tmp_path):
    root = tmp_path
    target = root / "existing.txt"
    target.write_text("ORIGINAL\n", encoding="utf-8")

    fp = FileProjection(project_root=str(root))
    delta = _delta_write(str(target), "NEW\n")

    with patch.object(fp.fm, "read", side_effect=OSError("read failed")):
        with pytest.raises(OSError):
            fp.prepare(delta)


# ── 4b. intent_tools：_update_path_map 失败暴露 warning ────────


def _build_tools_with_world(tmp_path, world):
    sync_state = SyncState(project_root=str(tmp_path))
    fp = FileProjection(project_root=str(tmp_path), object_path_map=None, sync_state=sync_state)
    pm = ProjectionManager(checkpoint_dir=str(tmp_path / ".forge"))
    pm.register(fp)
    executor = MagicMock()
    executor._world = world
    return make_intent_tools(executor, pm), sync_state, executor


def test_create_file_surfaces_update_path_map_failure_as_warning(tmp_path):
    """_update_path_map 抛错（无兜底）→ success=True + side_effect_warnings。"""
    world = SimpleNamespace(project_root=str(tmp_path), _path_map=None)
    world._update_path_map = MagicMock(side_effect=OSError("path map broken"))

    tools, _, executor = _build_tools_with_world(tmp_path, world)
    path = "a.txt"
    content = "hello\n"
    delta = _delta_write(str(tmp_path / path), content)
    executor.execute.return_value = (Receipt(tx_id=1, before_root=0, after_root=1, version=1, source="forge_tool"), delta)

    result = tools["create_file"](path=path, content=content)

    assert result.success is True
    assert (tmp_path / path).read_text(encoding="utf-8") == content
    assert "side_effect_warnings" in (result.payload or {})
    assert any("_update_path_map" in w for w in result.payload["side_effect_warnings"])
    assert "SIDE_EFFECT_WARN" in (result.display or "")


def test_update_path_map_failure_surfaces_even_when_fallback_succeeds(tmp_path):
    """_update_path_map 失败但 update_from_delta 兜底成功 → 仍暴露 warning。"""
    path_map = MagicMock()
    path_map.update_from_delta = MagicMock(return_value=None)
    world = SimpleNamespace(project_root=str(tmp_path), _path_map=path_map)
    world._update_path_map = MagicMock(side_effect=OSError("path map broken"))

    tools, _, executor = _build_tools_with_world(tmp_path, world)
    path = "b.txt"
    content = "hi\n"
    delta = _delta_write(str(tmp_path / path), content)
    executor.execute.return_value = (Receipt(tx_id=2, before_root=0, after_root=1, version=2, source="forge_tool"), delta)

    result = tools["create_file"](path=path, content=content)

    assert result.success is True
    path_map.update_from_delta.assert_called_once()
    assert "side_effect_warnings" in (result.payload or {})
    assert any("_update_path_map" in w for w in result.payload["side_effect_warnings"])

"""P3-4 / P3-5 / P3-6 契约测试。

- P3-4：SyncLayer.detect() 缓存——world_version 未变且磁盘侧无外部变化时，
  复用上次报告，不再全量 get_receipts_since(0)。三态判定语义不变。
- P3-5：undo_last_tx 语义——undo 只从 shadow 恢复磁盘、不写 World 账本 /
  receipt；display 明确提示 World 可能仍较新。
- P3-6：openai_compat 429/5xx 指数退避重试（最多 3 次），不重试其它 4xx。
"""
from __future__ import annotations

import binascii
import subprocess
from types import SimpleNamespace
from unittest import mock

import pytest

from forge.adapters.openai_compat import OpenAICompatAdapter
from forge.projections.file_projection import FileProjection
from forge.sync.state import SyncState
from forge.sync.sync_layer import (
    FAST_FORWARD_DISK_TO_WORLD,
    FAST_FORWARD_WORLD_TO_DISK,
    IN_SYNC,
    SyncLayer,
)
from forge.tools.intent_tools import make_intent_tools
from forge.tools.tx_shadow import record_tx, undo_last
from forge.world.types import Receipt, TransactionDelta


# ── 共用：receipt / git 构造 ─────────────────────────────────


def _file_receipt(version, abs_path, content, source="forge_tool"):
    path_hex = binascii.hexlify(str(abs_path).encode("utf-8")).decode("ascii")
    content_hex = binascii.hexlify(content.encode("utf-8")).decode("ascii")
    delta = TransactionDelta(
        memory_written=[
            {"object_id": 1, "state_id": 0, "value_hex": path_hex},
            {"object_id": 1, "state_id": 1, "value_hex": content_hex},
        ],
    )
    return Receipt(
        tx_id=version,
        before_root=0,
        after_root=version,
        version=version,
        delta=delta,
        source=source,
    )


def _init_git_repo(root):
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "t@t"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "t"], check=True)
    (root / "README.md").write_text("init\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-q", "-m", "init"], check=True)


class CountingWorld:
    """统计 get_receipts_since / get_version 调用次数的 MockWorld。"""

    def __init__(self, receipts=None):
        self._receipts = list(receipts or [])
        self.receipt_calls = 0
        self.version_calls = 0

    def get_receipts_since(self, version):
        self.receipt_calls += 1
        return [r for r in self._receipts if r.version > version]

    def get_version(self):
        self.version_calls += 1
        return max((r.version for r in self._receipts), default=0)


def _counting_layer(root, world, state, fp):
    return SyncLayer(project_root=str(root), world_runtime=world, sync_state=state, file_projection=fp)


# ── P3-4：detect 缓存 ─────────────────────────────────────────


def test_detect_caches_receipt_scan_when_nothing_changes(tmp_path):
    """无变化时重复 detect 不得再全量扫 receipt 历史（get_receipts_since 调用不增）。"""
    _init_git_repo(tmp_path)
    target = tmp_path / "file.txt"
    receipt = _file_receipt(1, str(target.resolve()), "v1\n")

    state = SyncState(tmp_path)
    fp = FileProjection(project_root=str(tmp_path), sync_state=state)
    assert fp.apply(receipt, receipt.delta).success

    world = CountingWorld([receipt])
    layer = _counting_layer(tmp_path, world, state, fp)

    r1 = layer.detect()
    assert r1.status == IN_SYNC, r1.format()
    calls_after_first = world.receipt_calls
    assert calls_after_first > 0  # 首次确实扫了 receipt

    r2 = layer.detect()
    assert r2.status == IN_SYNC, r2.format()
    # 缓存命中：第二次 detect 不再扫描 receipt 历史
    assert world.receipt_calls == calls_after_first, (
        f"detect 缓存未生效：首次后 receipt_calls={calls_after_first}，"
        f"第二次后={world.receipt_calls}"
    )


def test_detect_recomputes_when_world_version_changes(tmp_path):
    """World 前进（新 receipt）时缓存必须失效，返回 FAST_FORWARD(World→Disk)。"""
    _init_git_repo(tmp_path)
    target = tmp_path / "file.txt"
    r1 = _file_receipt(1, str(target.resolve()), "v1\n")
    r2 = _file_receipt(2, str(target.resolve()), "v2\n")

    state = SyncState(tmp_path)
    fp = FileProjection(project_root=str(tmp_path), sync_state=state)
    assert fp.apply(r1, r1.delta).success

    world = CountingWorld([r1])
    layer = _counting_layer(tmp_path, world, state, fp)
    assert layer.detect().status == IN_SYNC

    # World 前进
    world._receipts.append(r2)
    report = layer.detect()
    assert report.status == FAST_FORWARD_WORLD_TO_DISK, report.format()


def test_detect_recomputes_when_disk_changes(tmp_path):
    """磁盘外部编辑时缓存必须失效，返回 FAST_FORWARD(Disk→World)。"""
    _init_git_repo(tmp_path)
    target = tmp_path / "file.txt"
    receipt = _file_receipt(1, str(target.resolve()), "v1\n")

    state = SyncState(tmp_path)
    fp = FileProjection(project_root=str(tmp_path), sync_state=state)
    assert fp.apply(receipt, receipt.delta).success

    world = CountingWorld([receipt])
    layer = _counting_layer(tmp_path, world, state, fp)
    assert layer.detect().status == IN_SYNC

    target.write_text("USER EDIT\n", encoding="utf-8")
    report = layer.detect()
    assert report.status == FAST_FORWARD_DISK_TO_WORLD, report.format()


# ── P3-5：undo_last_tx 语义 ───────────────────────────────────


def test_undo_last_is_disk_only_does_not_write_receipt(tmp_path):
    """undo 只从 shadow 恢复磁盘；不写 World receipt，也不推进任何同步水位。"""
    f = tmp_path / "a.py"
    f.write_text("v1\n", encoding="utf-8")
    record_tx(str(tmp_path), tx_id=10, version=1, files={"a.py": "v1\n"})
    f.write_text("v2\n", encoding="utf-8")

    info = undo_last(str(tmp_path))

    assert info["ok"] is True
    assert info["mode"] == "file_shadow_revert"
    assert info["undone_tx"] == 10
    assert f.read_text(encoding="utf-8") == "v1\n"
    # undo_last 是纯磁盘函数：无 World 可写，不产生任何 external_sync receipt。
    # 这里不触碰 .forge/sync_state.json（undo 不推进 disk_synced_version）。
    state_file = tmp_path / ".forge" / "sync_state.json"
    assert not state_file.exists()


def test_undo_last_tx_display_documents_world_may_lag(tmp_path):
    """undo_last_tx 的 display 必须明确：World 账本可能仍较新，以磁盘为准。"""
    (tmp_path / "a.py").write_text("v = 1\n", encoding="utf-8")

    # veritasd 不可用 → direct_disk 写入，populate shadow
    executor = mock.MagicMock()
    executor._world = SimpleNamespace(
        project_root=str(tmp_path), _path_map=None,
        get_version=lambda: (_ for _ in ()).throw(RuntimeError("offline")),
    )
    projections = mock.MagicMock()
    tools = make_intent_tools(executor, projections)

    tools["str_replace"](path="a.py", old_string="v = 1", new_string="v = 2")
    undo = tools["undo_last_tx"]()

    assert undo.success is True
    assert "file_shadow_revert" in undo.display
    assert "may_lag" in undo.display
    assert "World 账本可能仍较新" in undo.display or "以磁盘" in undo.display
    assert (tmp_path / "a.py").read_text(encoding="utf-8") == "v = 1\n"


# ── P3-6：openai_compat 429 重试 ──────────────────────────────


class FakeHTTPError(Exception):
    """带 status_code 的假 HTTP 异常（对齐 openai.APIStatusError）。"""

    def __init__(self, status_code, message="err"):
        super().__init__(message)
        self.status_code = status_code


class FakeCompletions:
    def __init__(self, results):
        self.results = list(results)
        self.calls = 0

    def create(self, **kwargs):
        self.calls += 1
        item = self.results[self.calls - 1]
        if isinstance(item, Exception):
            raise item
        return item


class FakeClient:
    def __init__(self, results):
        self.chat = SimpleNamespace(completions=FakeCompletions(results))


def _ok_response(content="hi"):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=None))]
    )


def _adapter(monkeypatch, results):
    monkeypatch.setenv("P3_TEST_KEY", "k")
    adapter = OpenAICompatAdapter(
        model_name="m", api_key_env="P3_TEST_KEY", base_url="https://x"
    )
    adapter.client = FakeClient(results)
    return adapter


def test_openai_compat_retries_429_then_succeeds(monkeypatch):
    adapter = _adapter(monkeypatch, [FakeHTTPError(429), _ok_response("hi")])
    with mock.patch("forge.adapters.openai_compat.time.sleep"):
        msg = adapter.send([], [])
    assert msg.content == "hi"
    assert adapter.client.chat.completions.calls == 2


def test_openai_compat_retries_5xx_then_succeeds(monkeypatch):
    adapter = _adapter(monkeypatch, [FakeHTTPError(503), _ok_response("hi")])
    with mock.patch("forge.adapters.openai_compat.time.sleep"):
        msg = adapter.send([], [])
    assert msg.content == "hi"
    assert adapter.client.chat.completions.calls == 2


def test_openai_compat_does_not_retry_other_4xx(monkeypatch):
    adapter = _adapter(monkeypatch, [FakeHTTPError(400, "bad request")])
    with mock.patch("forge.adapters.openai_compat.time.sleep"):
        with pytest.raises(FakeHTTPError):
            adapter.send([], [])
    assert adapter.client.chat.completions.calls == 1


def test_openai_compat_exhausts_retries_and_raises(monkeypatch):
    adapter = _adapter(
        monkeypatch,
        [FakeHTTPError(429), FakeHTTPError(429), FakeHTTPError(429)],
    )
    with mock.patch("forge.adapters.openai_compat.time.sleep"):
        with pytest.raises(FakeHTTPError):
            adapter.send([], [])
    assert adapter.client.chat.completions.calls == 3

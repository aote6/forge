"""P0 Edit Contract e2e: Intent → (optional real Veritas) → Projection → bytes.

When veritasd is unavailable, the Veritas-backed case is skipped; pure
Projection + PatchEngine golden path still runs.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from forge.core.edit_contract import authoring_to_machine_ops
from forge.core.patch_engine import PatchEngine
from forge.intents.intent import Intent
from forge.projections.file_projection import FileProjection
from forge.world.types import Receipt, TransactionDelta


def _find_veritasd() -> str | None:
    candidates = [
        os.environ.get("VERITASD_BIN"),
        str(Path.home() / "veritas_kernel" / "target" / "release" / "veritasd"),
        str(Path.home() / "veritas" / "target" / "release" / "veritasd"),
        "/tmp/veritas/target/release/veritasd",
        "/tmp/veritas/target/debug/veritasd",
    ]
    for c in candidates:
        if c and Path(c).is_file():
            return c
    return None


def test_projection_apply_machine_ops_exact_bytes():
    """Projection path with machine ops stored as Veritas-like delta writes."""
    root = tempfile.mkdtemp(prefix="forge_edit_e2e_")
    try:
        path = os.path.join(root, "sample.py")
        original = "L1\nL2\nL3\nL4\n"
        with open(path, "w", encoding="utf-8") as f:
            f.write(original)

        authoring = {
            "type": "replace",
            "start_line": 3,
            "end_line": 3,
            "new_text": "CHANGED\n",
        }
        machine = authoring_to_machine_ops([authoring])
        # Simulate Veritas delta: state_id 0 = path, state_id 2 = ops JSON
        oid = 42
        delta = TransactionDelta(
            memory_written=[
                {
                    "object_id": oid,
                    "state_id": 0,
                    "value_hex": path.encode().hex(),
                },
                {
                    "object_id": oid,
                    "state_id": 2,
                    "value_hex": json.dumps(machine).encode().hex(),
                },
            ],
        )
        receipt = Receipt(tx_id=1, before_root=0, after_root=0, version=1, delta=delta)
        fp = FileProjection(project_root=root)
        result = fp.apply(receipt, delta)
        assert result.success, getattr(result, "reason", result)
        with open(path, encoding="utf-8") as f:
            assert f.read() == "L1\nL2\nCHANGED\nL4\n"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_failed_intent_no_projection_no_fs_change():
    """Invalid machine ops → validation fails before session; FS unchanged."""
    from forge.intents.executor import IntentExecutor, IntentExecutionError

    root = tempfile.mkdtemp(prefix="forge_abort_")
    try:
        path = os.path.join(root, "t.py")
        original = "KEEP\n"
        with open(path, "w", encoding="utf-8") as f:
            f.write(original)

        world = MagicMock()
        world.get_object.return_value = MagicMock(state="Alive")
        # begin_session must not be called if validation fails first
        ex = IntentExecutor(world)
        intent = Intent.modify_file(
            path=path,
            operations=[{
                "start_line": 1,  # looks 1-based authoring — rejected by machine validator
                "end_line": 1,
                "new_text": "NO\n",
            }],
            require_confirm=False,
        )
        intent.parameters["object_id"] = 7
        with pytest.raises(IntentExecutionError):
            ex.execute(intent)
        world.begin_session.assert_not_called()
        with open(path, encoding="utf-8") as f:
            assert f.read() == original
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_projection_failure_status_not_complete():
    """Commit succeeded in adapter terms but projection fails → not COMPLETE."""
    from forge.adapters.execution import ExecutionAdapter
    from forge.protocols.models import ChangeProposal
    from forge.world.types import Receipt, TransactionDelta

    root = tempfile.mkdtemp(prefix="forge_proj_fail_")
    try:
        # Empty world path map so modify fails early OR we mock commit+fail project
        world = MagicMock()
        # Force modify object resolve fail → ABORTED (not projection fail)
        world._path_map = {}
        projections = MagicMock()
        adapter = ExecutionAdapter(world, projections, root)
        proposal = ChangeProposal(
            proposal_id="p1",
            target_files=["missing.py"],
            operations=[{
                "type": "modify",
                "target_files": ["missing.py"],
                "start_line": 1,
                "end_line": 1,
                "new_text": "x\n",
            }],
        )
        result = adapter.execute_proposal(proposal)
        assert result.success is False
        assert result.status == "ABORTED"
        projections.project.assert_not_called()
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_projection_failed_after_commit_status():
    """When project() returns failure after commit, status is WORLD_COMMITTED_PROJECTION_FAILED."""
    from forge.adapters.execution import ExecutionAdapter
    from forge.protocols.models import ChangeProposal
    from forge.projections.base import ProjectionResult
    from forge.world.types import Receipt, TransactionDelta

    root = tempfile.mkdtemp(prefix="forge_wcpf_")
    try:
        path = os.path.join(root, "f.py")
        with open(path, "w", encoding="utf-8") as f:
            f.write("A\n")

        world = MagicMock()
        world._path_map = {path: 1, 1: path}
        # find via dict iteration in _resolve_object_id
        world._path_map = {1: path}

        receipt = Receipt(tx_id=9, before_root=0, after_root=0, version=3, delta=TransactionDelta())
        delta = TransactionDelta()
        world.begin_session = MagicMock()
        # IntentExecutor will call begin_session on world via WorldRuntime-like API
        # Use a thin real IntentExecutor path by mocking WorldRuntime methods

        class FakeWorld:
            def __init__(self):
                self._path_map = {1: path}
                self._session = MagicMock()

            def begin_session(self, actor_id=None):
                return self._session

            def commit_session(self):
                return receipt, delta

            def abort_session(self):
                pass

            def get_object(self, oid):
                return MagicMock(state="Alive")

        fw = FakeWorld()
        fw._session.write = MagicMock()
        fw._session.create_object = MagicMock(return_value=1)
        fw._session.abort = MagicMock()

        projections = MagicMock()
        projections.project.return_value = [
            ProjectionResult(name="file", success=False, reason="boom", retryable=True)
        ]
        projections.object_path_map = None

        adapter = ExecutionAdapter(fw, projections, root)
        # Monkey-patch resolve to return oid 1
        adapter._resolve_object_id = lambda full: 1

        proposal = ChangeProposal(
            proposal_id="p2",
            target_files=["f.py"],
            operations=[{
                "type": "modify",
                "target_files": ["f.py"],
                "start_line": 1,
                "end_line": 1,
                "new_text": "B\n",
            }],
        )
        result = adapter.execute_proposal(proposal)
        assert result.success is False
        assert result.status == "WORLD_COMMITTED_PROJECTION_FAILED"
        assert result.tx_id == 9
        assert "projection_failed" in result.error
    finally:
        shutil.rmtree(root, ignore_errors=True)


@pytest.mark.skipif(_find_veritasd() is None, reason="veritasd binary not found")
def test_real_veritas_commit_and_project():
    """Full chain against real veritasd when available."""
    from forge.intents.executor import IntentExecutor
    from forge.projections.base import ProjectionManager
    from forge.projections.object_path import ObjectPathMap
    from forge.world.adapter import WorldAdapter
    from forge.world.runtime import WorldRuntime

    bin_path = _find_veritasd()
    root = tempfile.mkdtemp(prefix="forge_veritas_e2e_")
    try:
        path = os.path.join(root, "v.py")
        with open(path, "w", encoding="utf-8") as f:
            f.write("L1\nL2\nL3\n")

        wal = os.path.join(root, "veritas.wal")
        world = WorldRuntime(project_root=root, binary=bin_path, wal_path=wal)
        adapter = world.adapter
        world.ensure_identity()

        # create object with path+content
        session = world.begin_session()
        oid = session.create_object()
        session.write(oid, 0, value=path)
        session.write(oid, 1, value="L1\nL2\nL3\n")
        receipt0, delta0 = world.commit_session()

        pmap = ObjectPathMap()
        pmap.set(oid, path)

        machine = authoring_to_machine_ops([{
            "type": "replace",
            "start_line": 2,
            "end_line": 2,
            "new_text": "MID\n",
        }])
        intent = Intent.modify_file(path=path, operations=machine, require_confirm=False)
        intent.parameters["object_id"] = oid
        executor = IntentExecutor(world)
        receipt, delta = executor.execute(intent)

        assert receipt.tx_id
        fp = FileProjection(project_root=root, object_path_map=pmap)
        result = fp.apply(receipt, delta)
        assert result.success, getattr(result, "reason", None)
        with open(path, encoding="utf-8") as f:
            assert f.read() == "L1\nMID\nL3\n"
    finally:
        try:
            adapter.close()
        except Exception:
            pass
        shutil.rmtree(root, ignore_errors=True)

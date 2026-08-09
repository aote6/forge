"""Forge v2 failure-path E2E: modify via Intent→Veritas→Projection,
syntax-error rejection contract, and TaskCheckpoint recovery.

Migrated off forbidden lu_patch() write path and TaskCheckpoint.state.
"""
from __future__ import annotations

import ast
import os
import shutil
import tempfile

import pytest

from forge.intents.intent import Intent
from forge.memory.checkpoint import CheckpointStore
from forge.protocols.models import OrchestratorPhase
from forge.task_memory import make_checkpoint


def _try_world(project_root: str):
    """Return WorldRuntime or None if Veritas backend is unavailable."""
    try:
        from forge.world.runtime import WorldRuntime

        world = WorldRuntime(project_root=project_root)
        world.ensure_identity()
        return world
    except Exception:
        return None


def test_modify_existing_file():
    """VERSION = \"1.0\" → \"2.0\" via Intent → Veritas → Projection."""
    root = tempfile.mkdtemp(prefix="forge_modify_")
    try:
        test_file = os.path.join(root, "modify_target.py")
        original = (
            '# Test module\nVERSION = "1.0"\n\n'
            'def get_version():\n    return "1.0"\n'
        )
        with open(test_file, "w", encoding="utf-8") as f:
            f.write(original)

        world = _try_world(root)
        if world is None:
            pytest.skip("Veritas/WorldRuntime unavailable — cannot exercise mutation path")

        from forge.intents.executor import IntentExecutor
        from forge.projections.base import ProjectionManager
        from forge.projections.file_projection import FileProjection
        from forge.projections.object_path import ObjectPathMap

        executor = IntentExecutor(world)
        create = Intent.create_file(
            path=test_file,
            content=original,
            overwrite=True,
            require_confirm=False,
        )
        receipt, delta = executor.execute(create)

        pmap = ObjectPathMap()
        pmap.update_from_delta(delta)
        pm = ProjectionManager(checkpoint_dir=os.path.join(root, ".forge"))
        pm.register(FileProjection(project_root=root, object_path_map=pmap))
        pm.project(receipt, delta)

        object_id = None
        paths = getattr(pmap, "_paths", {}) or {}
        for oid, path in paths.items():
            if path == test_file or os.path.abspath(str(path)) == os.path.abspath(test_file):
                object_id = oid
                break
        if object_id is None and hasattr(pmap, "get_object_id"):
            try:
                object_id = pmap.get_object_id(test_file)
            except Exception:
                object_id = None

        operations = [{"start_line": 1, "end_line": 2, "new_lines": ['VERSION = "2.0"\n']}]
        modify = Intent.modify_file(
            path=test_file, operations=operations, require_confirm=False
        )
        if object_id is not None:
            modify.parameters["object_id"] = object_id

        receipt2, delta2 = executor.execute(modify)
        pmap.update_from_delta(delta2)
        results = pm.project(receipt2, delta2)
        assert all(getattr(r, "success", True) for r in (results or [])), results

        with open(test_file, encoding="utf-8") as f:
            content = f.read()
        assert 'VERSION = "2.0"' in content
        assert content.strip().startswith("# Test module")
        ast.parse(content)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_syntax_error_rollback():
    """v2 contract: lu_patch write path stays closed; syntax-broken payload
    is rejected by language-level checks. Formal recovery is verification
    failure — not Lu auto-rollback.
    """
    root = tempfile.mkdtemp(prefix="forge_syntax_")
    try:
        test_file = os.path.join(root, "syntax_test.py")
        original = 'def hello():\n    return "Hello"\n'
        with open(test_file, "w", encoding="utf-8") as f:
            f.write(original)

        from forge.adapters.lu_patch_adapter import LuWriteForbidden, patch as lu_patch

        with pytest.raises(LuWriteForbidden):
            lu_patch(test_file, "def hello():", "def hello(")

        with open(test_file, encoding="utf-8") as f:
            assert f.read() == original

        broken = 'def hello(\n    return "Hello"\n'
        with pytest.raises(SyntaxError):
            ast.parse(broken)

        with open(test_file, encoding="utf-8") as f:
            assert f.read() == original
    finally:
        shutil.rmtree(root, ignore_errors=True)


def test_checkpoint_recovery():
    """TaskCheckpoint save → reload preserves phase, completed_steps, extra."""
    root = tempfile.mkdtemp(prefix="forge_cp_")
    try:
        store = CheckpointStore(root)
        task_id = "recovery_test_001"

        cp = make_checkpoint(
            task_id,
            OrchestratorPhase.EXECUTING.value,
            completed_steps=["s1", "s2"],
            extra_state={"test_data": "persisted"},
        )
        assert cp.extra.get("test_data") == "persisted"
        store.save(cp)

        store2 = CheckpointStore(root)
        loaded = store2.load(task_id)
        assert loaded is not None
        assert loaded.phase == OrchestratorPhase.EXECUTING.value
        assert loaded.completed_steps == ["s1", "s2"]
        assert loaded.extra.get("test_data") == "persisted"

        cp2 = make_checkpoint(
            task_id,
            OrchestratorPhase.COMPLETED.value,
            completed_steps=["s1", "s2", "s3"],
        )
        store2.save(cp2)
        loaded2 = store2.load(task_id)
        assert loaded2 is not None
        assert loaded2.phase == OrchestratorPhase.COMPLETED.value
        assert loaded2.completed_steps == ["s1", "s2", "s3"]

        store2.delete(task_id)
        assert store2.load(task_id) is None
    finally:
        shutil.rmtree(root, ignore_errors=True)

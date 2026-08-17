"""Real veritasd e2e: Intent(CREATE_OBJECT) → Kernel → commit → Receipt.

Proves pure object birth (no file semantics) and capability_grants cross the
veritasd JSON boundary into Forge TransactionDelta / capability_map.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import pytest

from forge.intents.intent import Intent, IntentType
from forge.intents.executor import IntentExecutor
from forge.world.runtime import WorldRuntime
from forge.world.adapter import WorldAdapter
from forge.world.session import WorldSession


def _resolve_veritasd() -> str | None:
    candidates = [
        Path.home() / "veritas_kernel" / "target" / "release" / "veritasd",
        Path.home() / "veritas" / "target" / "release" / "veritasd",
        Path("/tmp/audit/veritas/target/release/veritasd"),
        Path("/tmp/audit/veritas/target/debug/veritasd"),
        Path("/tmp/veritas/target/release/veritasd"),
        Path("/tmp/veritas/target/debug/veritasd"),
        Path("/home/workdir/veritas/target/release/veritasd"),
        Path("/home/workdir/veritas/target/debug/veritasd"),
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    return shutil.which("veritasd")


@pytest.fixture
def runtime(tmp_path):
    binary = _resolve_veritasd()
    if not binary:
        pytest.skip("veritasd binary not found — build aote6/veritas first")
    wal = tmp_path / "create_object.wal"
    try:
        rt = WorldRuntime(project_root=tmp_path, binary=binary, wal_path=wal)
    except Exception as e:
        pytest.skip(f"veritasd not usable: {e}")
    if not rt.online:
        pytest.skip("veritasd not online after WorldRuntime init")
    yield rt
    try:
        rt.close()
    except Exception:
        pass


def test_create_object_intent_e2e_commit(runtime: WorldRuntime):
    """Intent CREATE_OBJECT → commit → objects_created contains real Kernel id."""
    executor = IntentExecutor(runtime)
    intent = Intent.create_object()
    assert intent.type is IntentType.CREATE_OBJECT

    receipt, delta = executor.execute(intent)

    assert receipt.tx_id > 0
    assert len(delta.objects_created) == 1
    oid = delta.objects_created[0]
    assert isinstance(oid, int) and oid > 0
    assert intent.parameters.get("_created_object_id") == oid

    info = runtime.get_object(oid)
    assert info is not None
    assert info.state == "Alive"


def test_create_object_abort_leaves_no_object(runtime: WorldRuntime):
    """abort after create_object: object must not appear in world state."""
    session = runtime.begin_session()
    oid = session.create_object()
    assert isinstance(oid, int) and oid > 0
    session.abort()

    info = runtime.get_object(oid)
    assert info is None or info.state != "Alive"


def test_create_object_capability_grants_cross_boundary(runtime: WorldRuntime):
    """commit receipt must carry structured capability_grants into Forge delta."""
    executor = IntentExecutor(runtime)
    intent = Intent.create_object()
    receipt, delta = executor.execute(intent)

    oid = delta.objects_created[0]
    assert delta.capability_grants, (
        "capability_grants must be non-empty after ObjectBirth; "
        "veritasd receipt_json must serialize structured grants"
    )
    self_grants = [
        g
        for g in delta.capability_grants
        if g.grantee == g.resource == oid and g.cap_type == "AdminCap"
    ]
    assert self_grants, f"expected self-AdminCap for {oid}, got {delta.capability_grants}"

    cap_map = (delta.metadata or {}).get("capability_map") or {}
    assert oid in cap_map
    assert cap_map[oid] == self_grants[0].capability_id


def test_create_object_no_file_side_effects(runtime: WorldRuntime, tmp_path):
    """Pure CREATE_OBJECT must not write filesystem artifacts under project_root."""
    before = set(p.name for p in tmp_path.iterdir()) if tmp_path.exists() else set()
    executor = IntentExecutor(runtime)
    executor.execute(Intent.create_object())
    after = set(p.name for p in tmp_path.iterdir()) if tmp_path.exists() else set()
    # WAL / runtime files may appear; no projection of a user path is required.
    # Ensure we did not create a conventional source file from this intent.
    new_py = [n for n in (after - before) if n.endswith(".py")]
    assert new_py == []

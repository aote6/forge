"""P2 e2e: Forge WorldAdapter / WorldSession thin adaptation of tx_capability_grant.

Real path: Forge → WorldAdapter → veritasd JSONL → Veritas Kernel → CapabilityGrant.

Semantics under test:
  A creates B, C → commit
  B (no capability) link(B, C) → commit fails
  A grant(B, capability=link, resource=C) → commit
  B new session link(B, C) → commit succeeds
  grantor=A, grantee/holder=B, A != B
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from forge.world.adapter import WorldAdapter, WorldAdapterError
from forge.world.session import WorldSession


def _resolve_veritasd() -> str | None:
    candidates = [
        Path.home() / "veritas_kernel" / "target" / "release" / "veritasd",
        Path.home() / "veritas" / "target" / "release" / "veritasd",
        Path("/home/workdir/veritas/target/release/veritasd"),
        Path("/home/workdir/veritas/target/debug/veritasd"),
        Path("/tmp/veritas/target/release/veritasd"),
        Path("/tmp/veritas/target/debug/veritasd"),
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    # fall back to PATH
    import shutil
    found = shutil.which("veritasd")
    return found


@pytest.fixture
def adapter(tmp_path):
    binary = _resolve_veritasd()
    if not binary:
        pytest.skip("veritasd binary not found — build aote6/veritas first")
    wal = tmp_path / "p2_capgrant.wal"
    a = WorldAdapter(project_root=tmp_path, binary=binary, wal_path=wal)
    try:
        assert a.ping()
    except Exception as e:
        pytest.skip(f"veritasd not usable: {e}")
    yield a
    a.close()


def test_p2_capability_grant_a_grants_b_on_c(adapter: WorldAdapter):
    """A grant B link capability on C; unauthorized B fails; authorized B succeeds."""
    # --- A attaches identity ---
    a = adapter.attach_identity()
    assert isinstance(a, int) and a > 0

    # --- A creates B and C, commit ---
    sid0 = adapter.tx_begin(actor_id=a)
    session0 = WorldSession(adapter, sid0, a)
    b = session0.create_object()
    c = session0.create_object()
    assert a != b and a != c and b != c
    receipt0, _ = session0.commit()
    assert receipt0.tx_id > 0

    # --- Unauthorized: B attempts link(B, C) → commit must fail ---
    sid_bad = adapter.tx_begin(actor_id=b)
    session_bad = WorldSession(adapter, sid_bad, b)
    session_bad.link(b, c, link_type="owns")  # stages only
    with pytest.raises(WorldAdapterError):
        session_bad.commit()

    # --- A grants B link capability on C ---
    sid1 = adapter.tx_begin(actor_id=a)
    session1 = WorldSession(adapter, sid1, a)
    session1.grant(grantor=a, grantee=b, capability_type="link", resource=c)
    receipt1, _ = session1.commit()
    assert receipt1.tx_id > 0

    # --- Authorized: B new session can link(B, C) ---
    sid2 = adapter.tx_begin(actor_id=b)
    session2 = WorldSession(adapter, sid2, b)
    session2.link(b, c, link_type="owns")
    receipt2, delta2 = session2.commit()
    assert receipt2.tx_id > 0

    # Optional: confirm link exists via adapter
    links = adapter.get_links()
    assert any(
        (l.from_id == b and l.to_id == c)
        for l in links
    ), f"expected link B→C after authorized commit; got {links}"


def test_p2_adapter_tx_capability_grant_direct(adapter: WorldAdapter):
    """Adapter method alone: send cmd=tx_capability_grant with correct fields."""
    a = adapter.attach_identity()
    sid = adapter.tx_begin(actor_id=a)
    b = adapter.tx_create_object(sid)
    c = adapter.tx_create_object(sid)
    adapter.tx_commit(sid)

    sid_g = adapter.tx_begin(actor_id=a)
    # must not raise
    adapter.tx_capability_grant(
        session_id=sid_g,
        grantor=a,
        grantee=b,
        capability_type="link",
        resource=c,
    )
    adapter.tx_commit(sid_g)

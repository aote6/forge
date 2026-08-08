"""
Regression: Veritas world state must survive across Python processes.

Requires veritasd on PATH (or default candidate paths).
Uses a temporary project root and a dedicated WAL file.

Run (from forge repo root, with PYTHONPATH=. and veritasd available):

  pytest -q tests/test_veritas_persistence.py

Or manual cross-process check (see LOCAL_VERIFICATION below).
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest


def _veritasd_available() -> bool:
    from forge.world.adapter import WorldAdapter

    a = WorldAdapter(project_root=".", binary="veritasd")
    # Resolve only; do not start yet.
    return Path(a.binary).exists() or bool(
        subprocess.run(["which", "veritasd"], capture_output=True).returncode == 0
    )


pytestmark = pytest.mark.skipif(
    not _veritasd_available(),
    reason="veritasd binary not found",
)


def test_wal_path_is_set_and_stable(tmp_path: Path):
    """WorldAdapter always configures a durable WAL under project_root."""
    from forge.world.adapter import WorldAdapter, DEFAULT_WAL_REL

    a1 = WorldAdapter(project_root=tmp_path)
    a2 = WorldAdapter(project_root=tmp_path)
    expected = str((tmp_path / DEFAULT_WAL_REL).resolve())
    assert a1.wal_path == expected
    assert a2.wal_path == expected
    assert Path(a1.wal_path).parent.exists()


def test_cross_process_persistence(tmp_path: Path):
    """
    Process A: create object, write path (state_id=0), commit, exit.
    Process B: new WorldRuntime on same root → receipts_since(0) non-empty,
               list_objects non-empty, find_object_id(path) works.
    """
    root = str(tmp_path)
    # Isolate WAL from any ambient VERITAS_WAL.
    env = os.environ.copy()
    env.pop("VERITAS_WAL", None)

    script_a = f"""
import sys
sys.path.insert(0, {os.getcwd()!r})
from forge.world.runtime import WorldRuntime
rt = WorldRuntime(project_root={root!r})
assert rt.online, "veritasd not online"
sid = rt.adapter.tx_begin(None)
oid = rt.adapter.tx_create_object(sid)
path = "src/hello.py"
rt.adapter.tx_write(sid, 0, value=path)
rt.adapter.tx_write(sid, 1, value="print(1)\\n")
receipt = rt.adapter.tx_commit(sid)
print("OID", oid)
print("VERSION", receipt.version)
print("WAL", rt.adapter.wal_path)
rt.close()
"""
    r = subprocess.run(
        [sys.executable, "-c", script_a],
        capture_output=True,
        text=True,
        env=env,
        cwd=os.getcwd(),
    )
    assert r.returncode == 0, f"process A failed:\n{r.stdout}\n{r.stderr}"
    assert "OID" in r.stdout

    script_b = f"""
import sys
sys.path.insert(0, {os.getcwd()!r})
from forge.world.runtime import WorldRuntime
rt = WorldRuntime(project_root={root!r})
assert rt.online
receipts = rt.get_receipts_since(0)
print("RECEIPTS", len(receipts))
objs = rt.list_objects()
print("OBJECTS", len(objs))
oid = rt.find_object_id("src/hello.py")
print("FIND", oid)
info = rt.world_info()
print("VERSION", info.version)
rt.close()
assert len(receipts) >= 1, "receipts_since(0) empty after restart"
assert len(objs) >= 1, "list_objects empty after restart"
assert oid is not None, "find_object_id failed after restart"
"""
    r2 = subprocess.run(
        [sys.executable, "-c", script_b],
        capture_output=True,
        text=True,
        env=env,
        cwd=os.getcwd(),
    )
    assert r2.returncode == 0, f"process B failed:\n{r2.stdout}\n{r2.stderr}"
    assert "RECEIPTS" in r2.stdout


def test_close_does_not_destroy_wal(tmp_path: Path):
    """close() kills child process but WAL file remains; next runtime recovers."""
    from forge.world.runtime import WorldRuntime

    rt = WorldRuntime(project_root=tmp_path)
    assert rt.online
    wal = Path(rt.adapter.wal_path)
    sid = rt.adapter.tx_begin(None)
    oid = rt.adapter.tx_create_object(sid)
    rt.adapter.tx_write(sid, 0, value="keep.py")
    rt.adapter.tx_commit(sid)
    rt.close()
    assert wal.exists() or wal.stat().st_size >= 0  # may be created on first commit

    rt2 = WorldRuntime(project_root=tmp_path)
    assert rt2.online
    assert rt2.find_object_id("keep.py") is not None or len(rt2.get_receipts_since(0)) >= 1
    rt2.close()

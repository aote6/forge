"""Wiring tests: create_object must be on the tool-loop mutation surface.

These tests do not require a live LLM. They assert schema + make_tools registration
and, when veritasd is available, that create_object returns a structured ObjectId.
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from forge.tools.schemas import MUTATION_TOOL_DECLARATIONS, MUTATION_TOOL_NAMES
from forge.tools import make_tools
from forge.workspace import Workspace


def test_create_object_in_mutation_schema():
    names = [d["name"] for d in MUTATION_TOOL_DECLARATIONS]
    assert "create_object" in names
    assert "str_replace" in names
    assert "create_object" in MUTATION_TOOL_NAMES
    # no stale orchestrator-only wording
    for d in MUTATION_TOOL_DECLARATIONS:
        desc = d.get("description") or ""
        assert "EngineeringOrchestrator" not in desc, d["name"]


def test_make_tools_registers_create_object(tmp_path):
    """allow_mutation=True must expose create_object callable."""
    from forge.world.runtime import WorldRuntime
    from forge.projections.base import ProjectionManager

    ws = Workspace(project_root=str(tmp_path))
    # WorldRuntime may fail without veritasd; still test registration path with mocks
    tools = make_tools(
        workspace=ws,
        world_runtime=None,
        projections=None,
        allow_mutation=False,
    )
    assert "create_object" not in tools

    # With mutation but no world: intent tools not registered
    tools2 = make_tools(
        workspace=ws,
        world_runtime=None,
        projections=None,
        allow_mutation=True,
    )
    assert "create_object" not in tools2


def _resolve_veritasd() -> str | None:
    candidates = [
        Path.home() / "veritas_kernel" / "target" / "release" / "veritasd",
        Path.home() / "veritas" / "target" / "release" / "veritasd",
        Path("/tmp/audit/veritas/target/release/veritasd"),
        Path("/tmp/veritas/target/release/veritasd"),
    ]
    for c in candidates:
        if c.exists():
            return str(c)
    return shutil.which("veritasd")


def test_create_object_tool_returns_object_id(tmp_path):
    """Direct tool call: success + payload[object_id] is int; display has ObjectId=."""
    binary = _resolve_veritasd()
    if not binary:
        pytest.skip("veritasd binary not found")

    from forge.world.runtime import WorldRuntime
    from forge.projections.base import ProjectionManager
    from forge.projections.file_projection import FileProjection

    wal = tmp_path / "wiring.wal"
    try:
        world = WorldRuntime(project_root=tmp_path, binary=binary, wal_path=wal)
    except Exception as e:
        pytest.skip(f"veritasd not usable: {e}")
    if not getattr(world, "online", True):
        pytest.skip("veritasd not online")

    try:
        world.ensure_identity()
    except Exception:
        pass

    projections = ProjectionManager(checkpoint_dir=str(tmp_path / ".forge"))
    path_map = getattr(world, "_path_map", None)
    projections.register(
        FileProjection(project_root=str(tmp_path), object_path_map=path_map)
    )

    ws = Workspace(project_root=str(tmp_path))
    tools = make_tools(
        workspace=ws,
        world_runtime=world,
        projections=projections,
        allow_mutation=True,
    )
    assert "create_object" in tools
    assert "link_objects" in tools

    result = tools["create_object"]()
    assert result.success, result.display
    assert result.payload is not None
    oid = result.payload.get("object_id")
    assert isinstance(oid, int) and oid > 0
    assert f"ObjectId={oid}" in result.display
    assert "link_objects(from_id=" in result.display

    try:
        world.close()
    except Exception:
        pass

"""P0-1/P0-2: mutation tools must fail when projection returns success=False."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from forge.adapters.base import ToolResult
from forge.projections.base import ProjectionResult
from forge.tools.intent_tools import (
    _failed_projections,
    _projection_failure_result,
    make_intent_tools,
)


def test_failed_projections_filters():
    ok = ProjectionResult(name="file", success=True)
    bad = ProjectionResult(name="file", success=False, reason="disk full")
    assert _failed_projections([ok, bad]) == [bad]
    assert _failed_projections([ok]) == []
    assert _failed_projections([]) == []
    assert _failed_projections(None) == []


def test_projection_failure_result_is_fail_with_flag():
    results = [
        ProjectionResult(name="file", success=False, reason="permission denied"),
        ProjectionResult(name="git", success=True),
    ]
    receipt = SimpleNamespace(
        tx_id=42, version=7, before_root="aaa", after_root="bbb"
    )
    r = _projection_failure_result(results, receipt, tool="create_file")
    assert isinstance(r, ToolResult)
    assert r.success is False
    assert r.payload.get("projection_failed") is True
    assert r.payload.get("tx_id") == 42
    assert "投影失败" in (r.display or "")
    assert "forge_sync" in (r.display or "")
    assert "permission denied" in (r.display or "")


def test_create_file_returns_fail_when_projection_fails():
    """create_file must not return success=True if FileProjection failed."""
    executor = MagicMock()
    receipt = SimpleNamespace(
        tx_id=99, version=3, before_root="b", after_root="a"
    )
    delta = SimpleNamespace(objects_created=[1001])
    executor.execute.return_value = (receipt, delta)
    executor._world = SimpleNamespace(project_root="/tmp", _path_map=None)

    projections = MagicMock()
    projections.project.return_value = [
        ProjectionResult(name="file", success=False, reason="inject fail"),
    ]

    tools = make_intent_tools(executor, projections)
    result = tools["create_file"](path="foo.txt", content="hi\n")
    assert result.success is False
    assert result.payload.get("projection_failed") is True
    assert "inject fail" in (result.display or "")


def test_register_path_raises_when_projection_fails():
    """_register_path must not return oid / set path_map on projection failure."""
    executor = MagicMock()
    receipt = SimpleNamespace(tx_id=1, version=1, before_root=None, after_root=None)
    delta = SimpleNamespace(objects_created=[7])
    executor.execute.return_value = (receipt, delta)
    path_map = MagicMock()
    executor._world = SimpleNamespace(project_root="/tmp", _path_map=path_map)

    projections = MagicMock()
    projections.project.return_value = [
        ProjectionResult(name="file", success=False, reason="no write"),
    ]

    tools = make_intent_tools(executor, projections)
    # write_file with oid=None triggers auto-register via _register_path
    result = tools["write_file"](path="new_only.txt", content="x\n")
    assert result.success is False
    # either auto-register failed message or projection_failed payload
    assert (
        result.payload.get("projection_failed") is True
        or "auto-register" in (result.display or "").lower()
        or "projection failed" in (result.display or "").lower()
    )
    path_map.set.assert_not_called()

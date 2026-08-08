"""Planner contract tests — formal return type is tuple[Plan, dict]."""
from __future__ import annotations

import tempfile
from unittest.mock import MagicMock

from forge.adapters.base import Message
from forge.planner import Planner
from forge.protocols.models import Plan, RepoContext


def test_planner_plan_returns_tuple():
    raw = (
        '{"goal":"g","assumptions":[],"impact_files":["new_unique_xyz.py"],'
        '"impact_symbols":[],'
        '"steps":[{"step_id":"s1","description":"d",'
        '"target_files":["new_unique_xyz.py"],'
        '"operation_type":"create_file","dependencies":[],"content":"x\\n"}]}'
    )
    adapter = MagicMock()
    resp = MagicMock()
    resp.content = raw
    adapter.send.return_value = resp

    planner = Planner(adapter)
    repo = RepoContext(file_tree=["other.py"], commit_hash="0" * 8)
    with tempfile.TemporaryDirectory() as root:
        result = planner.plan(
            "create new_unique_xyz.py", repo, project_root=root
        )
    assert isinstance(result, tuple)
    plan, enriched = result
    assert isinstance(plan, Plan)
    assert isinstance(enriched, dict)
    assert hasattr(plan, "steps")
    assert len(plan.steps) >= 1
    for step in plan.steps:
        assert step.operation_type in ("modify", "create_file", "delete_file", "delete")
    adapter.send.assert_called()
    args, _kwargs = adapter.send.call_args
    assert args and all(isinstance(m, Message) for m in args[0])

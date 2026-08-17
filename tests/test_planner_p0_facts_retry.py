"""P0 Planner: Repository Facts injection + fail-closed Validator + validation retry.

Architecture guard:
- Machine provides facts only (DEFINED / NOT_DEFINED + location).
- LLM decides operation_type.
- Validator ACCEPT/REJECT only — never semantic repair of operation_type/target_files.
- On REJECT, Planner retries (max 2) with task + facts + prior Plan JSON + reason.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from forge.planner import (
    Planner,
    PlanValidationError,
    PLANNER_SYSTEM_PROMPT,
)
from forge.context.planning import compute_repository_facts
from forge.plan_validator import PlanValidator, PlanValidationError as VErr
from forge.protocols.repository import RepoContext
from forge.context.index import RepositoryIndex, Symbol


class RecordingAdapter:
    """Returns successive canned plans; records prompts."""

    def __init__(self, responses: list):
        self._responses = list(responses)
        self.calls = []

    def send(self, messages, tools=None):
        self.calls.append(list(messages))
        if not self._responses:
            content = json.dumps({
                "goal": "fallback",
                "steps": [{
                    "step_id": "step_1",
                    "description": "noop",
                    "operation_type": "create_object",
                    "target_files": [],
                    "dependencies": [],
                }],
            })
        else:
            content = self._responses.pop(0)
            if not isinstance(content, str):
                content = json.dumps(content)
        return SimpleNamespace(content=content)


def _create_object_plan():
    return {
        "goal": "创建 World 对象",
        "steps": [{
            "step_id": "step_1",
            "description": "创建对象",
            "operation_type": "create_object",
            "target_files": [],
            "dependencies": [],
        }],
    }


def _bad_modify_plan():
    return {
        "goal": "创建 World 对象",
        "steps": [{
            "step_id": "step_1",
            "description": "误判为改源码",
            "operation_type": "modify",
            "target_files": ["forge/world/adapter.py"],
            "dependencies": [],
            # missing start_line/end_line/new_text → validator REJECT
        }],
    }


def test_build_repository_facts_defined_and_not_defined():
    idx = RepositoryIndex(snapshot_id="test")
    idx.symbols = [
        Symbol(
            name="tx_create_object",
            qualified_name="forge.world.adapter.tx_create_object",
            kind="function",
            file_path="forge/world/adapter.py",
            start_line=10,
            end_line=20,
        )
    ]
    task = "创建一个新的 World 对象，不要调用 tx_create_object 改源码"
    facts = compute_repository_facts(idx, ['tx_create_object'])
    assert "tx_create_object" in facts
    assert "DEFINED" in facts
    assert "forge/world/adapter.py" in facts
    # World may or may not be extracted; facts must not suggest operation_type
    assert "operation_type" not in facts or "no operation_type advice" in facts.lower() or "facts only" in facts.lower()


def test_build_repository_facts_no_index():
    facts = compute_repository_facts(None, ["FooBar"])
    assert "FooBar" in facts
    assert "NOT_DEFINED" in facts
    assert "no repository index" in facts


def test_planner_prompt_includes_repository_facts():
    task = "创建一个新的 World 对象，不修改任何源码文件。"
    repo = RepoContext(file_tree=[], changed_files=[], recent_changes=[])
    adapter = RecordingAdapter([_create_object_plan()])
    planner = Planner(adapter)
    plan, enriched = planner.plan(task, repo, project_root=".")
    assert plan.steps[0].operation_type == "create_object"
    assert "repository_facts" in enriched
    # prompt must contain Repository Facts section
    user_content = adapter.calls[0][1].content
    assert "Repository Facts" in user_content


def test_validator_create_object_nonempty_targets_rejected():
    v = PlanValidator(".")
    repo = RepoContext(file_tree=["a.py"])
    plan = {
        "goal": "create obj",
        "steps": [{
            "step_id": "step_1",
            "description": "create",
            "operation_type": "create_object",
            "target_files": ["a.py"],
            "dependencies": [],
        }],
    }
    with pytest.raises(VErr) as cm:
        v.validate(plan, repo)
    assert "create_object" in str(cm.value)
    assert "target_files" in str(cm.value)


def test_validator_modify_empty_targets_rejected():
    v = PlanValidator(".")
    repo = RepoContext(file_tree=[])
    plan = {
        "goal": "edit",
        "steps": [{
            "step_id": "step_1",
            "description": "edit",
            "operation_type": "modify",
            "target_files": [],
            "dependencies": [],
            "start_line": 1,
            "end_line": 1,
            "new_text": "x",
        }],
    }
    with pytest.raises(VErr) as cm:
        v.validate(plan, repo)
    assert "target_files" in str(cm.value)


def test_validator_create_object_empty_targets_accepted():
    v = PlanValidator(".")
    repo = RepoContext(file_tree=[])
    plan = {
        "goal": "create obj",
        "steps": [{
            "step_id": "step_1",
            "description": "create",
            "operation_type": "create_object",
            "target_files": [],
            "dependencies": [],
        }],
    }
    p, _ = v.validate(plan, repo)
    assert p.steps[0].operation_type == "create_object"
    assert p.steps[0].target_files == []


def test_planner_retry_includes_prior_plan_and_reason():
    """On Validator REJECT, next prompt must include prior Plan JSON + reason."""
    task = "创建一个新的 World 对象，不修改任何源码文件。"
    repo = RepoContext(
        file_tree=["forge/world/adapter.py"],
        changed_files=[],
        recent_changes=[],
    )
    # First response: invalid modify (missing line fields) → REJECT
    # Second response: valid create_object → ACCEPT
    adapter = RecordingAdapter([_bad_modify_plan(), _create_object_plan()])
    planner = Planner(adapter)
    plan, enriched = planner.plan(task, repo, project_root=".")
    assert plan.steps[0].operation_type == "create_object"
    assert plan.steps[0].target_files == []
    assert enriched.get("planner_attempts", 0) >= 2
    assert len(adapter.calls) == 2
    retry_prompt = adapter.calls[1][1].content
    assert "VALIDATION REJECTION" in retry_prompt
    assert "rejection reason" in retry_prompt
    assert "modify" in retry_prompt  # prior plan content
    assert "Repository Facts" in retry_prompt
    assert task in retry_prompt


def test_planner_retry_exhausted_raises():
    task = "创建一个新的 World 对象"
    repo = RepoContext(file_tree=["forge/world/adapter.py"])
    # Always return the same invalid plan
    bad = _bad_modify_plan()
    adapter = RecordingAdapter([bad, bad, bad, bad])
    planner = Planner(adapter)
    with pytest.raises(PlanValidationError) as cm:
        planner.plan(task, repo, project_root=".")
    assert "after" in str(cm.value).lower() or "attempt" in str(cm.value).lower()
    # 1 initial + 2 retries = 3 calls
    assert len(adapter.calls) == 3


def test_no_semantic_repair_in_validator():
    """Validator must not rewrite modify → create_object."""
    v = PlanValidator(".")
    repo = RepoContext(file_tree=["forge/world/adapter.py"])
    with tempfile.TemporaryDirectory() as td:
        # create a file so modify could theoretically pass path checks if lines present
        root = Path(td)
        p = root / "forge" / "world"
        p.mkdir(parents=True)
        (p / "adapter.py").write_text("def tx_create_object():\n    pass\n", encoding="utf-8")
        v = PlanValidator(td)
        repo = RepoContext(file_tree=["forge/world/adapter.py"])
        plan = {
            "goal": "创建对象",
            "steps": [{
                "step_id": "step_1",
                "description": "创建",
                "operation_type": "modify",
                "target_files": ["forge/world/adapter.py"],
                "start_line": 1,
                "end_line": 1,
                "new_text": "x = 1\n",
                "dependencies": [],
            }],
        }
        # Valid structurally as modify — validator must ACCEPT as modify, not rewrite
        out, _ = v.validate(plan, repo)
        assert out.steps[0].operation_type == "modify"
        assert out.steps[0].target_files == ["forge/world/adapter.py"]


def test_system_prompt_mentions_facts_not_rewrite():
    assert "create_object" in PLANNER_SYSTEM_PROMPT
    assert "Repository Facts" in PLANNER_SYSTEM_PROMPT or "DEFINED" in PLANNER_SYSTEM_PROMPT

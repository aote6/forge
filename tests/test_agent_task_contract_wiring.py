"""R3: AgentTask contract entry wiring.

Chain: schema → spawn_subagent → AgentTask → constraint_enforcer → sub-loop → AgentResult
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from forge.adapters.base import ToolCall, ToolResult
from forge.agent_abi import AgentResult, AgentTask, build_subagent_user_message
from forge.constraint_enforcer import enforce
from forge.subagent import filter_schemas_for_subagent, run_subagent
from forge.tools.schemas import (
    CONTROL_PLANE_TOOL_DECLARATIONS,
    EXECUTION_PLANE_TOOL_DECLARATIONS,
    MUTATION_TOOL_DECLARATIONS,
    READ_ONLY_TOOL_DECLARATIONS,
)


def _spawn_decl() -> dict:
    return next(
        d for d in CONTROL_PLANE_TOOL_DECLARATIONS if d["name"] == "spawn_subagent"
    )


def _spawn_task(**kwargs):
    """Directly exercise the extracted Runtime helper."""
    from forge.runtime import _build_agent_task_from_spawn_args
    return _build_agent_task_from_spawn_args(**kwargs)


def test_spawn_subagent_schema_exposes_agent_task_fields():
    decl = _spawn_decl()
    props = decl["parameters"]["properties"]
    for key in ("goal", "done_when", "stop_when", "not_allowed", "scope", "max_steps"):
        assert key in props, f"missing schema field: {key}"
    required = set(decl["parameters"].get("required") or [])
    assert {"goal", "done_when", "stop_when"} <= required
    # legacy task-only entry removed from schema
    assert "task" not in props


def test_agent_task_fields_from_spawn_args():
    task = _spawn_task(
        goal="fix typo in src",
        done_when="src/a.py contains fixed string",
        stop_when="str_replace succeeded on src/a.py",
        not_allowed=["write", "delete"],
        scope=["src/", "tests/"],
        max_steps=8,
    )
    assert task.goal == "fix typo in src"
    assert task.done_when == "src/a.py contains fixed string"
    assert task.stop_when == "str_replace succeeded on src/a.py"
    assert task.max_steps == 8
    assert task.constraints["not_allowed"] == ["write", "delete"]
    assert task.constraints["scope"]["paths"] == ["src/", "tests/"]


def test_not_allowed_blocks_before_execution():
    task = _spawn_task(not_allowed=["write"])
    d = enforce("write_file", {"path": "src/a.py", "content": "x"}, task.constraints)
    assert d.allowed is False
    assert "not_allowed" in d.reason


def test_scope_out_of_bounds_blocked():
    task = _spawn_task(scope={"paths": ["src/"]})
    d = enforce("read_file", {"path": "outside/x.py"}, task.constraints)
    assert d.allowed is False
    assert "scope.paths" in d.reason


def test_scope_in_bounds_allowed():
    task = _spawn_task(scope={"paths": ["src/"]})
    d = enforce("read_file", {"path": "src/a.py"}, task.constraints)
    assert d.allowed is True


def test_not_allowed_takes_priority_over_scope():
    task = _spawn_task(
        not_allowed=["write"],
        scope={"paths": ["src/"]},
    )
    d = enforce("write_file", {"path": "src/a.py", "content": "x"}, task.constraints)
    assert d.allowed is False
    assert "not_allowed" in d.reason


def test_done_when_stop_when_in_subagent_user_message():
    task = AgentTask(
        goal="g",
        done_when="file exists",
        stop_when="read succeeded",
        max_steps=3,
    )
    msg = build_subagent_user_message(task)
    assert "g" in msg
    assert "done_when" in msg and "file exists" in msg
    assert "stop_when" in msg and "read succeeded" in msg


def test_run_subagent_enforces_constraints_before_tool_body(tmp_path: Path):
    """not_allowed / scope deny must prevent tool functions from running."""
    executed: list[str] = []

    class Adapter:
        def __init__(self):
            self.n = 0

        def send(self, messages, schemas):
            self.n += 1
            if self.n == 1:
                return MagicMock(
                    content="STOP_WHEN: not_met",
                    tool_calls=[
                        ToolCall(
                            id="1",
                            name="write_file",
                            arguments={"path": "src/a.py", "content": "x"},
                        )
                    ],
                )
            if self.n == 2:
                return MagicMock(
                    content="STOP_WHEN: not_met",
                    tool_calls=[
                        ToolCall(
                            id="2",
                            name="read_file",
                            arguments={"path": "outside/b.py"},
                        )
                    ],
                )
            return MagicMock(
                content=(
                    "STOP_WHEN: met\nCONCLUSION: constraints blocked\n"
                    "EVIDENCE: (无)\nUNCERTAIN: (无)\nNEXT: (无)"
                ),
                tool_calls=None,
            )

    tools = {
        "write_file": lambda path, content="": (
            executed.append(f"write:{path}") or ToolResult.ok(display="written")
        ),
        "read_file": lambda path, start=1, end=0: (
            executed.append(f"read:{path}") or ToolResult.ok(display="content")
        ),
        "str_replace": lambda **k: ToolResult.ok(display="ok"),
    }
    schemas = filter_schemas_for_subagent(
        list(READ_ONLY_TOOL_DECLARATIONS) + list(MUTATION_TOOL_DECLARATIONS)
    )
    task = AgentTask(
        goal="try forbidden ops",
        done_when="n/a",
        stop_when="constraints blocked",
        constraints={
            "not_allowed": ["write"],
            "scope": {"paths": ["src/"]},
        },
        max_steps=5,
    )
    result = run_subagent(Adapter(), tools, schemas, task, project_root=tmp_path)
    assert isinstance(result, AgentResult)
    assert executed == []


def test_run_subagent_allows_in_scope_read(tmp_path: Path):
    executed: list[str] = []

    class Adapter:
        def __init__(self):
            self.n = 0

        def send(self, messages, schemas):
            self.n += 1
            if self.n == 1:
                return MagicMock(
                    content="STOP_WHEN: not_met",
                    tool_calls=[
                        ToolCall(
                            id="1",
                            name="read_file",
                            arguments={"path": "src/ok.py"},
                        )
                    ],
                )
            return MagicMock(
                content=(
                    "STOP_WHEN: met\nCONCLUSION: read ok\n"
                    "EVIDENCE: (无)\nUNCERTAIN: (无)\nNEXT: (无)"
                ),
                tool_calls=None,
            )

    tools = {
        "read_file": lambda path, start=1, end=0: (
            executed.append(path) or ToolResult.ok(display="ok body")
        ),
    }
    schemas = filter_schemas_for_subagent(list(READ_ONLY_TOOL_DECLARATIONS))
    task = AgentTask(
        goal="read src",
        done_when="content seen",
        stop_when="read returned",
        constraints={"scope": {"paths": ["src/"]}},
        max_steps=5,
    )
    result = run_subagent(Adapter(), tools, schemas, task, project_root=tmp_path)
    assert isinstance(result, AgentResult)
    assert executed == ["src/ok.py"]


def test_run_subagent_passes_done_when_stop_when_into_messages():
    seen: list[str] = []

    class Adapter:
        def send(self, messages, schemas):
            for m in messages:
                if getattr(m, "role", None) == "user":
                    seen.append(m.content or "")
            return MagicMock(
                content=(
                    "STOP_WHEN: met\nCONCLUSION: done\n"
                    "EVIDENCE: (无)\nUNCERTAIN: (无)\nNEXT: (无)"
                ),
                tool_calls=None,
            )

    task = AgentTask(
        goal="inspect",
        done_when="observed X",
        stop_when="tool returned",
        max_steps=2,
    )
    run_subagent(Adapter(), {}, list(READ_ONLY_TOOL_DECLARATIONS), task)
    assert seen
    blob = "\n".join(seen)
    assert "observed X" in blob
    assert "tool returned" in blob
    assert "inspect" in blob


def test_scope_string_and_list_folding():
    assert _spawn_task(scope="forge/").constraints["scope"]["paths"] == ["forge/"]
    assert _spawn_task(scope=["a/", "b/"]).constraints["scope"]["paths"] == ["a/", "b/"]
    assert _spawn_task(scope={"paths": ["x/"]}).constraints["scope"]["paths"] == ["x/"]


def test_scope_comma_string_split():
    task = _spawn_task(scope="forge/subtask_checkpoint.py, forge")
    assert task.constraints["scope"]["paths"] == [
        "forge/subtask_checkpoint.py",
        "forge",
    ]


def test_scope_single_string_not_split():
    task = _spawn_task(scope="forge/subtask_checkpoint.py")
    assert task.constraints["scope"]["paths"] == ["forge/subtask_checkpoint.py"]


def test_legacy_task_alias_fills_goal_only():
    task = _spawn_task(
        task="legacy goal",
        done_when="d",
        stop_when="s",
        max_steps=3,
    )
    assert task.goal == "legacy goal"
    assert task.done_when == "d"
    assert task.stop_when == "s"
    assert task.max_steps == 3
    assert task.constraints == {}

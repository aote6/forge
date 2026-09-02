"""Phase 1: Control Plane / Execution Plane tool-surface isolation."""
from __future__ import annotations

from pathlib import Path

from forge.runtime import _default_tool_schemas
from forge.subagent import filter_schemas_for_subagent
from forge.tool_action_map import TOOL_ACTION_MAP_BY_NAME
from forge.tools.schemas import (
    CONTROL_PLANE_TOOL_DECLARATIONS,
    CONTROL_PLANE_TOOLS,
    EXECUTION_PLANE_TOOL_DECLARATIONS,
    EXECUTION_PLANE_TOOLS,
    MAIN_READ_ONLY_TOOL_NAMES,
    MUTATION_TOOL_DECLARATIONS,
    READ_ONLY_TOOL_DECLARATIONS,
    RECONCILIATION_TOOL_DECLARATIONS,
    SUBMIT_PLAN_TOOL_NAME,
)


def test_control_plane_minimum_set():
    required = {
        "spawn_subagent",
        "verify_tool_call",
        "todo_write",
        "todo_list",
        "submit_plan",
    }
    assert required <= CONTROL_PLANE_TOOLS
    assert SUBMIT_PLAN_TOOL_NAME in CONTROL_PLANE_TOOLS


def test_planes_disjoint():
    assert CONTROL_PLANE_TOOLS.isdisjoint(EXECUTION_PLANE_TOOLS)


def test_main_schemas_are_control_plus_main_read_only():
    """P1: main sees control plane + MAIN_READ_ONLY; no mutation/execution writes."""
    names = {s["name"] for s in _default_tool_schemas()}
    assert CONTROL_PLANE_TOOLS <= names
    assert MAIN_READ_ONLY_TOOL_NAMES <= names
    assert names == CONTROL_PLANE_TOOLS | MAIN_READ_ONLY_TOOL_NAMES
    for required in ("read_file", "search_code", "spawn_subagent"):
        assert required in names
    for banned in ("write_file", "str_replace", "run_command", "forge_sync"):
        assert banned not in names


def test_execution_plane_contains_engineering_tools():
    for required in (
        "read_file",
        "write_file",
        "run_command",
        "str_replace",
        "forge_sync",
        "run_test_structured",
    ):
        assert required in EXECUTION_PLANE_TOOLS


def test_execution_plane_excludes_control_tools():
    for banned in (
        "spawn_subagent",
        "verify_tool_call",
        "todo_write",
        "todo_list",
        "submit_plan",
    ):
        assert banned not in EXECUTION_PLANE_TOOLS


def test_filter_schemas_for_subagent_drops_control():
    mixed = (
        list(CONTROL_PLANE_TOOL_DECLARATIONS)
        + list(EXECUTION_PLANE_TOOL_DECLARATIONS)
    )
    filtered = filter_schemas_for_subagent(mixed)
    names = {s["name"] for s in filtered}
    assert names == EXECUTION_PLANE_TOOLS
    assert "spawn_subagent" not in names
    assert "read_file" in names


def test_execution_decls_match_union_of_surfaces():
    expected = (
        {d["name"] for d in READ_ONLY_TOOL_DECLARATIONS}
        | {d["name"] for d in MUTATION_TOOL_DECLARATIONS}
        | {d["name"] for d in RECONCILIATION_TOOL_DECLARATIONS}
    )
    assert EXECUTION_PLANE_TOOLS == expected


def test_all_plane_tools_registered_in_action_map():
    missing = (CONTROL_PLANE_TOOLS | EXECUTION_PLANE_TOOLS) - set(
        TOOL_ACTION_MAP_BY_NAME
    )
    assert missing == set(), f"unmapped tools: {sorted(missing)}"


def test_spawn_closure_filters_tools_to_execution(tmp_path: Path):
    """Mirror Runtime spawn_subagent filtering without constructing full Runtime."""
    from forge.tools import make_tools
    from forge.workspace import Workspace

    class _DummySync:
        def sync(self):
            return None

    ws = Workspace(project_root=str(tmp_path))
    tools = make_tools(
        workspace=ws,
        allow_mutation=True,
        world_runtime=object(),
        projections=object(),
        sync_layer=_DummySync(),
    )
    tools["spawn_subagent"] = lambda task, max_steps=15: None

    schemas = list(EXECUTION_PLANE_TOOL_DECLARATIONS)
    sub_tools = {k: v for k, v in tools.items() if k in EXECUTION_PLANE_TOOLS}
    schema_names = {s["name"] for s in schemas}
    assert schema_names <= EXECUTION_PLANE_TOOLS
    assert set(sub_tools) <= EXECUTION_PLANE_TOOLS
    assert "spawn_subagent" not in sub_tools
    assert "spawn_subagent" not in schema_names
    assert "read_file" in sub_tools or "read_file" in schema_names


def test_write_confirm_tools_unreachable_from_main_loop():
    """记录当前架构不变量：WRITE_CONFIRM 工具集与主 AI 可见工具集不相交。

    _main_tool_policy_denied 在主循环工具分派时，先于 _write_strategy 判定拦截
    所有 mutation/reconciliation 工具并 continue（见 forge/runtime.py 主循环）。
    因此 _WRITE_CONFIRM_TOOLS（= MUTATION_TOOL_NAMES | RECONCILIATION_TOOL_NAMES，
    减去 recovery 与 forge_sync）里的工具永远不会走到 strategy == "WRITE_CONFIRM"
    那段代码。

    若此测试失败，说明 CONTROL_PLANE_TOOL_DECLARATIONS 或 MAIN_READ_ONLY 混入了
    mutation/reconciliation 工具——WRITE_CONFIRM 分支将被重新激活，需要人工审查
    该分支的行为是否仍然正确（它已经很久没有被真实路径覆盖过）。
    """
    from forge.runtime import _WRITE_CONFIRM_TOOLS, _default_tool_schemas

    main_visible = {s["name"] for s in _default_tool_schemas()}
    assert main_visible.isdisjoint(_WRITE_CONFIRM_TOOLS)

"""P1-8: schemas.py 与 make_tools 实际暴露面的对齐契约测试。

生产调用路径 = Runtime.run → _run_conversation，schemas 决定 LLM 可见工具，
executor.tools（make_tools + Runtime 追加 spawn_subagent）决定可调用实现。

本测试用「伪 world/projections/sync_layer」构建与生产一致的完整 registry
（构建期不触碰 veritasd），锁定四条不变式：
  1) schema 暴露的工具都有对应实现；
  2) 每个注册实现要么公开（有 schema）要么显式列入 INTERNAL_TOOL_NAMES；
  3) internal/test 工具不会泄漏进 Planner/LLM schema；
  4) tool name 与参数定义一致（schema 参数 ⊆ 实现签名）。
"""
from __future__ import annotations

import inspect
from pathlib import Path

from forge.tools import make_tools
from forge.tools.schemas import (
    CONTROL_PLANE_TOOL_DECLARATIONS,
    CONTROL_PLANE_TOOLS,
    EXECUTION_PLANE_TOOLS,
    INTERNAL_TOOL_NAMES,
    MUTATION_TOOL_DECLARATIONS,
    MUTATION_TOOL_NAMES,
    READ_ONLY_TOOL_DECLARATIONS,
    RECONCILIATION_TOOL_DECLARATIONS,
    SUBMIT_PLAN_DECLARATION,
    SUBMIT_PLAN_TOOL_NAME,
)
from forge.workspace import Workspace


class _DummySync:
    def sync(self):
        return None


def _full_registry(tmp_path: Path) -> tuple[dict, set[str]]:
    """Build the production-equivalent tool registry without touching veritasd.

    mirrors Runtime.__init__: make_tools(allow_mutation=True, sync_layer=...) + spawn_subagent.
    """
    ws = Workspace(project_root=str(tmp_path))
    tools = make_tools(
        workspace=ws,
        allow_mutation=True,
        world_runtime=object(),  # intent tools are lazy; never called during build
        projections=object(),
        sync_layer=_DummySync(),
    )

    def spawn_subagent(task: str, max_steps: int = 15):
        return None  # real impl lives in Runtime; signature is the contract

    tools["spawn_subagent"] = spawn_subagent
    return tools


def _schema_names() -> set[str]:
    """All role-visible schema names (control ∪ execution)."""
    return set(CONTROL_PLANE_TOOLS) | set(EXECUTION_PLANE_TOOLS)


def test_schema_tools_all_have_implementations(tmp_path: Path):
    registry = set(_full_registry(tmp_path).keys())
    # submit_plan is schema-visible but intercepted in _run_conversation (not registered).
    missing = _schema_names() - registry - {SUBMIT_PLAN_TOOL_NAME}
    assert missing == set(), f"schema 暴露但无实现: {sorted(missing)}"


def test_public_implementations_have_schema_or_are_internal(tmp_path: Path):
    registry = set(_full_registry(tmp_path).keys())
    extra = registry - _schema_names()
    # 每个注册实现：要么公开（有 schema），要么显式在 INTERNAL_TOOL_NAMES。
    assert extra == set(INTERNAL_TOOL_NAMES), (
        f"注册但未分类的实现:\n"
        f"  多余(不在 INTERNAL_TOOL_NAMES): {sorted(extra - INTERNAL_TOOL_NAMES)}\n"
        f"  缺失(INTERNAL_TOOL_NAMES 里已不存在): {sorted(INTERNAL_TOOL_NAMES - extra)}"
    )


def test_internal_tools_not_exposed_to_llm():
    schema = _schema_names()
    leaked = set(INTERNAL_TOOL_NAMES) & schema
    assert leaked == set(), f"internal/test 工具泄漏进 schema: {sorted(leaked)}"


def test_internal_tools_still_registered(tmp_path: Path):
    registry = set(_full_registry(tmp_path).keys())
    missing = set(INTERNAL_TOOL_NAMES) - registry
    assert missing == set(), f"internal 工具未注册(可调用实现消失): {sorted(missing)}"


def test_read_only_and_mutation_surfaces_disjoint():
    ro = {d["name"] for d in READ_ONLY_TOOL_DECLARATIONS}
    mu = {d["name"] for d in MUTATION_TOOL_DECLARATIONS}
    assert ro.isdisjoint(mu), f"只读面与突变面重叠: {sorted(ro & mu)}"


def test_mastodon_side_effects_are_mutations_not_read_only():
    """post_toot / delete_toot 都是外部副作用，必须归 MUTATION，与只读面无关。"""
    ro = {d["name"] for d in READ_ONLY_TOOL_DECLARATIONS}
    mu = {d["name"] for d in MUTATION_TOOL_DECLARATIONS}
    assert "post_toot" in mu, "post_toot 有外部发帖副作用，必须归 MUTATION"
    assert "delete_toot" in mu, "delete_toot 有外部删除副作用，必须归 MUTATION"
    assert "post_toot" not in ro
    assert "delete_toot" not in ro


def test_mutation_tools_all_registered(tmp_path: Path):
    registry = set(_full_registry(tmp_path).keys())
    missing = set(MUTATION_TOOL_NAMES) - registry
    assert missing == set(), f"突变工具无实现: {sorted(missing)}"


def test_submit_plan_is_intercepted_not_registered(tmp_path: Path):
    """submit_plan 由 _run_conversation 拦截处理，不进入 ToolExecutor registry。"""
    registry = set(_full_registry(tmp_path).keys())
    assert SUBMIT_PLAN_DECLARATION["name"] == SUBMIT_PLAN_TOOL_NAME
    assert SUBMIT_PLAN_TOOL_NAME not in registry


def test_tool_name_and_params_consistent(tmp_path: Path):
    registry = _full_registry(tmp_path)
    decls = {d["name"]: d for d in list(READ_ONLY_TOOL_DECLARATIONS) + list(MUTATION_TOOL_DECLARATIONS) + list(CONTROL_PLANE_TOOL_DECLARATIONS)}
    problems = []
    for name, decl in decls.items():
        fn = registry.get(name)
        if fn is None:
            continue
        try:
            sig_params = set(inspect.signature(fn).parameters)
        except (TypeError, ValueError) as e:
            problems.append(f"{name}: 无法内省签名 ({e})")
            continue
        props = set((decl.get("parameters") or {}).get("properties") or {})
        extra = props - sig_params
        if extra:
            problems.append(f"{name}: schema 参数实现不接受: {sorted(extra)}")
    assert problems == [], "\n".join(problems)


def test_spawn_subagent_schema_matches_runtime_signature(tmp_path: Path):
    registry = _full_registry(tmp_path)
    sig = inspect.signature(registry["spawn_subagent"])
    decl = next(d for d in CONTROL_PLANE_TOOL_DECLARATIONS if d["name"] == "spawn_subagent")
    props = set((decl.get("parameters") or {}).get("properties") or {})
    assert props <= set(sig.parameters)

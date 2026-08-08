"""Priority 4: Planning Precision & Dependency Reasoning.

Tests machine impact derivation, dependency ordering, planner integration,
and Validator boundaries — with real RepositoryIndex, mocked LLM.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from forge.context.index import RepositoryIndex, extract_focus_symbols
from forge.context.planning import (
    apply_machine_impact_to_plan,
    compute_impact_set,
    explain_why_file_in_impact,
    format_impact_section,
    merge_impact,
    prioritize_content_files,
    topological_order_steps,
)
from forge.context.snapshot import take_snapshot
from forge.failures import FailureClass, FailureRecord, RepairConstraints, build_repair_constraints
from forge.plan_validator import PlanValidationError, PlanValidator
from forge.planner import Planner, PlanValidationError as PlannerValidationError
from forge.protocols.models import Plan, PlanStep, RepoContext


def _w(root: Path, rel: str, content: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def _fixture_callers(root: Path) -> None:
    _w(root, "core.py", "def calculate(x):\n    return x + 1\n")
    _w(
        root,
        "service_a.py",
        "from core import calculate\n\ndef run_a():\n    return calculate(1)\n",
    )
    _w(
        root,
        "service_b.py",
        "from core import calculate\n\ndef run_b():\n    return calculate(2)\n",
    )
    _w(root, "unrelated_a.py", "def noise():\n    return 0\n")
    _w(root, "unrelated_b.py", "def more_noise():\n    return 1\n")
    _w(root, "tests/test_core.py", "from core import calculate\n\ndef test_calc():\n    assert calculate(1) == 2\n")


def _fixture_ambiguous(root: Path) -> None:
    _w(root, "a.py", "def process(x):\n    return x\n")
    _w(root, "b.py", "def process(x):\n    return x * 2\n")
    _w(root, "c.py", "from a import process\n\ndef use():\n    return process(1)\n")


def _fixture_chain(root: Path) -> None:
    _w(root, "models.py", "class Item:\n    def __init__(self, name):\n        self.name = name\n")
    _w(
        root,
        "service.py",
        "from models import Item\n\ndef make(name):\n    return Item(name)\n",
    )
    _w(
        root,
        "controller.py",
        "from service import make\n\ndef handle(n):\n    return make(n)\n",
    )


def _mock_adapter(plan_dict: dict) -> MagicMock:
    adapter = MagicMock()
    adapter.send = MagicMock(
        return_value=SimpleNamespace(content=json.dumps(plan_dict))
    )
    return adapter


class TestComputeImpactSet(unittest.TestCase):
    def test_single_function_impact(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _w(root, "module_a.py", "def calculate():\n    return 1\n")
            _w(root, "test_module_a.py", "from module_a import calculate\n")
            idx = RepositoryIndex.build(str(root))
            m = compute_impact_set(idx, task="修复 calculate 的明显逻辑错误")
            self.assertIn("module_a.py", m["impact_files"])
            self.assertIn("calculate", m["impact_symbols"])
            # test file references calculate
            self.assertIn("test_module_a.py", m["impact_files"])

    def test_multi_caller_includes_all_services(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _fixture_callers(root)
            idx = RepositoryIndex.build(str(root))
            m = compute_impact_set(idx, task="修改 calculate 的 API")
            for f in ("core.py", "service_a.py", "service_b.py", "tests/test_core.py"):
                self.assertIn(f, m["impact_files"], msg=f"missing {f}")
            self.assertNotIn("unrelated_a.py", m["impact_files"])
            self.assertNotIn("unrelated_b.py", m["impact_files"])
            self.assertIn("calculate", m["impact_symbols"])
            self.assertIn("service_a.py", m["callers_by_symbol"].get("calculate", []))
            self.assertIn("service_b.py", m["callers_by_symbol"].get("calculate", []))

    def test_ambiguous_symbol_flagged(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _fixture_ambiguous(root)
            idx = RepositoryIndex.build(str(root))
            m = compute_impact_set(idx, focus_symbols=["process"])
            self.assertIn("process", m["ambiguous_symbols"])
            self.assertEqual(
                set(m["ambiguous_symbols"]["process"]), {"a.py", "b.py"}
            )
            # both defs + caller in impact
            self.assertIn("a.py", m["impact_files"])
            self.assertIn("b.py", m["impact_files"])
            self.assertIn("c.py", m["impact_files"])

    def test_no_index_empty_impact(self):
        m = compute_impact_set(None, task="fix calculate")
        self.assertEqual(m["impact_files"], [])

    def test_seed_files_included(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _w(root, "x.py", "def f():\n    pass\n")
            idx = RepositoryIndex.build(str(root))
            m = compute_impact_set(idx, task="noop", seed_files=["extra.py"])
            self.assertIn("extra.py", m["impact_files"])

    def test_deterministic(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _fixture_callers(root)
            idx = RepositoryIndex.build(str(root))
            a = compute_impact_set(idx, task="修改 calculate 的 API")
            b = compute_impact_set(idx, task="修改 calculate 的 API")
            self.assertEqual(a, b)


class TestTopologicalOrder(unittest.TestCase):
    def test_orders_by_dependencies(self):
        s1 = PlanStep(step_id="s1", description="def", target_files=["core.py"])
        s2 = PlanStep(
            step_id="s2",
            description="caller",
            target_files=["svc.py"],
            dependencies=["s1"],
        )
        s3 = PlanStep(
            step_id="s3",
            description="test",
            target_files=["t.py"],
            dependencies=["s2"],
        )
        # intentionally reverse input order
        ordered = topological_order_steps([s3, s2, s1])
        ids = [s.step_id for s in ordered]
        self.assertEqual(ids, ["s1", "s2", "s3"])

    def test_stable_without_deps(self):
        steps = [
            PlanStep(step_id="a", description="a", target_files=["a.py"]),
            PlanStep(step_id="b", description="b", target_files=["b.py"]),
        ]
        ordered = topological_order_steps(steps)
        self.assertEqual([s.step_id for s in ordered], ["a", "b"])

    def test_cycle_does_not_drop_steps(self):
        s1 = PlanStep(step_id="s1", description="a", target_files=["a.py"], dependencies=["s2"])
        s2 = PlanStep(step_id="s2", description="b", target_files=["b.py"], dependencies=["s1"])
        ordered = topological_order_steps([s1, s2])
        self.assertEqual(len(ordered), 2)


class TestPrioritizeContent(unittest.TestCase):
    def test_impact_first(self):
        tree = ["z.py", "core.py", "a.py", "svc.py"]
        ordered = prioritize_content_files(tree, ["core.py", "svc.py"], ["a.py"])
        self.assertEqual(ordered[:2], ["core.py", "svc.py"])
        self.assertIn("a.py", ordered)


class TestMergeAndExplain(unittest.TestCase):
    def test_merge_unions(self):
        machine = {"impact_files": ["a.py"], "impact_symbols": ["foo"]}
        files, syms = merge_impact(machine, ["b.py"], ["bar"])
        self.assertEqual(files, ["a.py", "b.py"])
        self.assertEqual(syms, ["foo", "bar"])

    def test_explain(self):
        machine = {
            "impact_files": ["core.py", "svc.py"],
            "definitions_by_symbol": {"calculate": ["core.py"]},
            "callers_by_symbol": {"calculate": ["svc.py"]},
        }
        self.assertTrue(any("defines" in r for r in explain_why_file_in_impact(machine, "core.py")))
        self.assertTrue(any("references" in r for r in explain_why_file_in_impact(machine, "svc.py")))


class TestPlannerMachineIntegration(unittest.TestCase):
    def test_planner_merges_callers_into_impact(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _fixture_callers(root)
            idx = RepositoryIndex.build(str(root))
            # LLM only mentions core.py — machine must still union callers
            plan_dict = {
                "goal": "change calculate API",
                "assumptions": [],
                "impact_files": ["core.py"],
                "impact_symbols": ["calculate"],
                "steps": [
                    {
                        "step_id": "s1",
                        "description": "change signature",
                        "target_files": ["core.py"],
                        "operation_type": "modify",
                        "start_line": 1,
                        "end_line": 2,
                        "new_text": "def calculate(x, y=0):\n    return x + y + 1\n",
                    }
                ],
            }
            planner = Planner(_mock_adapter(plan_dict))
            repo = RepoContext(
                file_tree=[
                    "core.py",
                    "service_a.py",
                    "service_b.py",
                    "unrelated_a.py",
                    "unrelated_b.py",
                    "tests/test_core.py",
                ]
            )
            plan, enriched = planner.plan(
                "修改 calculate 的 API",
                repo,
                project_root=str(root),
                index=idx,
            )
            for f in ("core.py", "service_a.py", "service_b.py"):
                self.assertIn(f, plan.impact_files)
            self.assertNotIn("unrelated_a.py", plan.impact_files)
            self.assertIn("machine_impact", enriched)
            self.assertIn("service_a.py", enriched["machine_impact"]["callers_by_symbol"]["calculate"])

    def test_planner_rejects_unrelated_modify_outside_impact(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _fixture_callers(root)
            idx = RepositoryIndex.build(str(root))
            plan_dict = {
                "goal": "change calculate",
                "steps": [
                    {
                        "step_id": "s1",
                        "description": "touch unrelated",
                        "target_files": ["unrelated_a.py"],
                        "operation_type": "modify",
                        "start_line": 1,
                        "end_line": 2,
                        "new_text": "def noise():\n    return 99\n",
                    }
                ],
            }
            planner = Planner(_mock_adapter(plan_dict))
            repo = RepoContext(
                file_tree=[
                    "core.py",
                    "service_a.py",
                    "service_b.py",
                    "unrelated_a.py",
                    "tests/test_core.py",
                ]
            )
            with self.assertRaises((PlanValidationError, PlannerValidationError)):
                planner.plan(
                    "修改 calculate 的 API",
                    repo,
                    project_root=str(root),
                    index=idx,
                )

    def test_multi_step_dependency_order(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _fixture_callers(root)
            idx = RepositoryIndex.build(str(root))
            plan_dict = {
                "goal": "migrate API",
                "impact_files": ["core.py", "service_a.py", "tests/test_core.py"],
                "steps": [
                    {
                        "step_id": "s_test",
                        "description": "update test",
                        "target_files": ["tests/test_core.py"],
                        "operation_type": "modify",
                        "dependencies": ["s_svc"],
                        "start_line": 1,
                        "end_line": 4,
                        "new_text": "from core import calculate\n\ndef test_calc():\n    assert calculate(1, 0) == 2\n",
                    },
                    {
                        "step_id": "s_svc",
                        "description": "update service",
                        "target_files": ["service_a.py"],
                        "operation_type": "modify",
                        "dependencies": ["s_def"],
                        "start_line": 1,
                        "end_line": 4,
                        "new_text": "from core import calculate\n\ndef run_a():\n    return calculate(1, 0)\n",
                    },
                    {
                        "step_id": "s_def",
                        "description": "update definition",
                        "target_files": ["core.py"],
                        "operation_type": "modify",
                        "dependencies": [],
                        "start_line": 1,
                        "end_line": 2,
                        "new_text": "def calculate(x, y=0):\n    return x + y + 1\n",
                    },
                ],
            }
            planner = Planner(_mock_adapter(plan_dict))
            repo = RepoContext(
                file_tree=["core.py", "service_a.py", "service_b.py", "tests/test_core.py"]
            )
            plan, _ = planner.plan(
                "将 calculate API 改成两参数并迁移调用方",
                repo,
                project_root=str(root),
                index=idx,
            )
            ids = [s.step_id for s in plan.steps]
            self.assertEqual(ids, ["s_def", "s_svc", "s_test"])

    def test_repair_constraints_still_enforced(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _fixture_callers(root)
            idx = RepositoryIndex.build(str(root))
            fail = FailureRecord(
                code=FailureClass.TEST_FAILURE.value,
                message="tests failed",
                files=["tests/test_core.py"],
            )
            rc = build_repair_constraints(fail, index=idx)
            plan_dict = {
                "goal": "fix tests",
                "steps": [
                    {
                        "step_id": "s1",
                        "description": "edit unrelated only",
                        "target_files": ["unrelated_a.py"],
                        "operation_type": "modify",
                        "start_line": 1,
                        "end_line": 2,
                        "new_text": "def noise():\n    return 0\n",
                    }
                ],
            }
            planner = Planner(_mock_adapter(plan_dict))
            repo = RepoContext(
                file_tree=["core.py", "service_a.py", "unrelated_a.py", "tests/test_core.py"]
            )
            with self.assertRaises((PlanValidationError, PlannerValidationError)):
                planner.plan(
                    "修复 calculator 的失败测试",
                    repo,
                    project_root=str(root),
                    index=idx,
                    failure=fail,
                    repair_constraints=rc,
                )

    def test_create_file_allowed_outside_impact(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _w(root, "core.py", "def calculate(x):\n    return x\n")
            idx = RepositoryIndex.build(str(root))
            plan_dict = {
                "goal": "add feature",
                "impact_files": ["core.py"],
                "steps": [
                    {
                        "step_id": "s1",
                        "description": "new module",
                        "target_files": ["new_feature.py"],
                        "operation_type": "create_file",
                        "content": "def feature():\n    return 1\n",
                    }
                ],
            }
            planner = Planner(_mock_adapter(plan_dict))
            repo = RepoContext(file_tree=["core.py"])
            plan, _ = planner.plan(
                "添加 new_feature 模块",
                repo,
                project_root=str(root),
                index=idx,
            )
            self.assertEqual(plan.steps[0].operation_type, "create_file")

    def test_content_priority_loads_impact_first(self):
        """Indirect: prioritize_content_files used; impact appears before unrelated."""
        ordered = prioritize_content_files(
            ["unrelated_a.py", "core.py", "service_a.py"],
            ["core.py", "service_a.py"],
            [],
        )
        self.assertEqual(ordered[0], "core.py")
        self.assertEqual(ordered[1], "service_a.py")


class TestApplyMachineImpact(unittest.TestCase):
    def test_apply_merges_and_orders(self):
        plan = Plan(
            plan_id="p",
            goal="g",
            impact_files=["core.py"],
            steps=[
                PlanStep(step_id="b", description="b", target_files=["svc.py"], dependencies=["a"]),
                PlanStep(step_id="a", description="a", target_files=["core.py"]),
            ],
        )
        machine = {
            "impact_files": ["core.py", "svc.py", "t.py"],
            "impact_symbols": ["calculate"],
            "ambiguous_symbols": {},
            "callers_by_symbol": {},
            "definitions_by_symbol": {},
        }
        apply_machine_impact_to_plan(plan, machine)
        self.assertEqual(plan.impact_files, ["core.py", "svc.py", "t.py"])
        self.assertEqual([s.step_id for s in plan.steps], ["a", "b"])


class TestImportChainImpact(unittest.TestCase):
    def test_models_change_sees_downstream_refs_to_Item(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _fixture_chain(root)
            idx = RepositoryIndex.build(str(root))
            m = compute_impact_set(idx, focus_symbols=["Item"])
            # models defines Item; service references Item
            self.assertIn("models.py", m["impact_files"])
            self.assertIn("service.py", m["impact_files"])
            # controller imports make, not Item directly — may or may not list controller
            # Name 'Item' refs only in service
            self.assertIn("service.py", m["callers_by_symbol"].get("Item", []))


class TestFormatImpactSection(unittest.TestCase):
    def test_contains_ambiguous_marker(self):
        section = format_impact_section(
            {
                "impact_files": ["a.py", "b.py"],
                "impact_symbols": ["process"],
                "ambiguous_symbols": {"process": ["a.py", "b.py"]},
                "callers_by_symbol": {"process": ["c.py"]},
                "definitions_by_symbol": {"process": ["a.py", "b.py"]},
            }
        )
        self.assertIn("AMBIGUOUS", section)
        self.assertIn("process", section)
        self.assertIn("callers of process", section)


class TestValidatorStillHardGate(unittest.TestCase):
    def test_impact_boundary_on_validator_alone(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _w(root, "core.py", "x\n")
            _w(root, "other.py", "y\n")
            v = PlanValidator(str(root))
            repo = RepoContext(file_tree=["core.py", "other.py"])
            bad = {
                "goal": "g",
                "impact_files": ["core.py"],
                "steps": [
                    {
                        "step_id": "s1",
                        "description": "bad",
                        "target_files": ["other.py"],
                        "operation_type": "modify",
                        "start_line": 1,
                        "end_line": 1,
                        "new_text": "z\n",
                    }
                ],
            }
            with self.assertRaises(PlanValidationError):
                v.validate(bad, repo)


if __name__ == "__main__":
    unittest.main()

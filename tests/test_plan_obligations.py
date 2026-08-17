"""Priority 7: Plan Obligation Coverage."""
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

from forge.context.index import RepositoryIndex
from forge.context.planning import (
    compute_impact_set,
    compute_obligations,
    is_runtime_only_plan,
    missing_required_obligations,
    plan_mutation_files,
    required_obligation_files,
    topological_order_steps,
)
from forge.failures import FailureClass, FailureRecord, RepairConstraints, build_repair_constraints
from forge.plan_validator import PlanValidationError, PlanValidator
from forge.planner import Planner
from forge.protocols.models import Plan, PlanStep, RepoContext


def _w(root: Path, rel: str, content: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def _fixture(root: Path) -> None:
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
    _w(root, "unrelated.py", "def noise():\n    return 0\n")
    _w(
        root,
        "tests/test_core.py",
        "from core import calculate\n\ndef test_calc():\n    assert calculate(1) == 2\n",
    )


def _mock_adapter(plan_dict: dict) -> MagicMock:
    adapter = MagicMock()
    adapter.send = MagicMock(
        return_value=SimpleNamespace(content=json.dumps(plan_dict))
    )
    return adapter


class TestComputeObligations(unittest.TestCase):
    def test_unique_definition_and_two_callers(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _fixture(root)
            idx = RepositoryIndex.build(str(root))
            obs = compute_obligations(idx, task="修改 calculate 的 API")
            req_files = required_obligation_files(obs)
            self.assertIn("core.py", req_files)
            self.assertIn("service_a.py", req_files)
            self.assertIn("service_b.py", req_files)
            # test is advisory
            test_obs = [o for o in obs if o["file"] == "tests/test_core.py"]
            self.assertTrue(test_obs)
            self.assertFalse(any(o["required"] for o in test_obs))
            # unrelated never
            self.assertNotIn("unrelated.py", req_files)

    def test_create_file_no_fake_obligations(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _w(root, "a.py", "def f():\n    pass\n")
            idx = RepositoryIndex.build(str(root))
            obs = compute_obligations(idx, task="添加新模块 new_feature")
            # no focus hit for new_feature definition
            self.assertEqual(required_obligation_files(obs), [])

    def test_ambiguous_not_required(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _w(root, "a.py", "def process(x):\n    return x\n")
            _w(root, "b.py", "def process(x):\n    return x * 2\n")
            idx = RepositoryIndex.build(str(root))
            obs = compute_obligations(idx, focus_symbols=["process"])
            self.assertTrue(obs)
            self.assertFalse(any(o.get("required") for o in obs))
            self.assertTrue(all(o.get("ambiguous") for o in obs if o["role"] == "definition"))

    def test_unrelated_not_obligation(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _fixture(root)
            idx = RepositoryIndex.build(str(root))
            machine = compute_impact_set(idx, task="修改 calculate 的 API")
            # impact should not include unrelated; obligations neither
            obs = compute_obligations(idx, task="修改 calculate 的 API", machine=machine)
            files = {o["file"] for o in obs}
            self.assertNotIn("unrelated.py", files)


class TestValidatorCoverage(unittest.TestCase):
    def test_only_definition_fails(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _fixture(root)
            idx = RepositoryIndex.build(str(root))
            obs = compute_obligations(idx, task="修改 calculate 的 API")
            v = PlanValidator(str(root))
            repo = RepoContext(
                file_tree=[
                    "core.py",
                    "service_a.py",
                    "service_b.py",
                    "unrelated.py",
                    "tests/test_core.py",
                ]
            )
            plan_dict = {
                "goal": "change API",
                "impact_files": [
                    "core.py",
                    "service_a.py",
                    "service_b.py",
                    "tests/test_core.py",
                ],
                "steps": [
                    {
                        "step_id": "s1",
                        "description": "only def",
                        "target_files": ["core.py"],
                        "operation_type": "modify",
                        "start_line": 1,
                        "end_line": 2,
                        "new_text": "def calculate(x, y=0):\n    return x + y + 1\n",
                    }
                ],
            }
            with self.assertRaises(PlanValidationError) as cm:
                v.validate(plan_dict, repo, obligations=obs)
            self.assertIn("obligation", str(cm.exception).lower())

    def test_full_coverage_passes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _fixture(root)
            idx = RepositoryIndex.build(str(root))
            obs = compute_obligations(idx, task="修改 calculate 的 API")
            v = PlanValidator(str(root))
            repo = RepoContext(
                file_tree=["core.py", "service_a.py", "service_b.py", "tests/test_core.py"]
            )
            plan_dict = {
                "goal": "change API",
                "impact_files": [
                    "core.py",
                    "service_a.py",
                    "service_b.py",
                    "tests/test_core.py",
                ],
                "steps": [
                    {
                        "step_id": "s_def",
                        "description": "def",
                        "target_files": ["core.py"],
                        "operation_type": "modify",
                        "start_line": 1,
                        "end_line": 2,
                        "new_text": "def calculate(x, y=0):\n    return x + y + 1\n",
                    },
                    {
                        "step_id": "s_a",
                        "description": "a",
                        "target_files": ["service_a.py"],
                        "operation_type": "modify",
                        "dependencies": ["s_def"],
                        "start_line": 1,
                        "end_line": 4,
                        "new_text": "from core import calculate\n\ndef run_a():\n    return calculate(1, 0)\n",
                    },
                    {
                        "step_id": "s_b",
                        "description": "b",
                        "target_files": ["service_b.py"],
                        "operation_type": "modify",
                        "dependencies": ["s_def"],
                        "start_line": 1,
                        "end_line": 4,
                        "new_text": "from core import calculate\n\ndef run_b():\n    return calculate(2, 0)\n",
                    },
                ],
            }
            plan, _ = v.validate(plan_dict, repo, obligations=obs)
            self.assertEqual(len(plan.steps), 3)
            self.assertEqual(missing_required_obligations(plan, obs), [])


class TestRepairIntersection(unittest.TestCase):
    def test_repair_demotes_outside_obligations(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _fixture(root)
            idx = RepositoryIndex.build(str(root))
            fail = FailureRecord(
                code=FailureClass.TEST_FAILURE.value,
                message="t",
                files=["tests/test_core.py"],
            )
            rc = build_repair_constraints(fail, index=idx)
            # force narrow repair to only tests/test_core.py + maybe impact expand
            rc.must_touch_files = ["tests/test_core.py"]
            rc.required_impact_files = ["tests/test_core.py", "core.py"]
            obs = compute_obligations(
                idx, task="修改 calculate 的 API", repair_constraints=rc
            )
            # service_a/b required should be demoted (outside repair)
            for o in obs:
                if o["file"] in ("service_a.py", "service_b.py"):
                    self.assertFalse(o["required"])
                    self.assertTrue(o.get("repair_conflict"))
            # core still required if in required_impact
            core = [o for o in obs if o["file"] == "core.py" and o["role"] == "definition"]
            self.assertTrue(core)
            self.assertTrue(core[0]["required"])


class TestPlannerIntegration(unittest.TestCase):
    def test_planner_rejects_incomplete_plan(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _fixture(root)
            idx = RepositoryIndex.build(str(root))
            plan_dict = {
                "goal": "change API",
                "impact_files": ["core.py", "service_a.py", "service_b.py"],
                "steps": [
                    {
                        "step_id": "s1",
                        "description": "only core",
                        "target_files": ["core.py"],
                        "operation_type": "modify",
                        "start_line": 1,
                        "end_line": 2,
                        "new_text": "def calculate(x, y=0):\n    return x + 1\n",
                    }
                ],
            }
            planner = Planner(_mock_adapter(plan_dict))
            repo = RepoContext(
                file_tree=["core.py", "service_a.py", "service_b.py", "unrelated.py"]
            )
            with self.assertRaises(PlanValidationError):
                planner.plan(
                    "修改 calculate 的 API",
                    repo,
                    project_root=str(root),
                    index=idx,
                )

    def test_topo_order_preserved_with_coverage(self):
        steps = [
            PlanStep(step_id="s_b", description="b", target_files=["service_b.py"], operation_type="modify", dependencies=["s_def"]),
            PlanStep(step_id="s_def", description="d", target_files=["core.py"], operation_type="modify",),
            PlanStep(step_id="s_a", description="a", target_files=["service_a.py"], operation_type="modify", dependencies=["s_def"]),
        ]
        ordered = topological_order_steps(steps)
        self.assertEqual(ordered[0].step_id, "s_def")



class TestCreateObjectRuntimeObligations(unittest.TestCase):
    """P7: pure create_object must not be forced to cover mutation obligations."""

    def _req_obs(self):
        return [
            {
                "file": "forge/world.py",
                "symbol": "World",
                "role": "definition",
                "required": True,
                "reason": "unique definition of World",
            },
            {
                "file": "forge/world/session.py",
                "symbol": "tx_create_object",
                "role": "caller",
                "required": True,
                "reason": "caller of tx_create_object",
            },
        ]

    def test_pure_create_object_accepts_despite_required_obligations(self):
        obs = self._req_obs()
        plan = Plan(
            plan_id="p",
            goal="create world object without source edits",
            steps=[
                PlanStep(
                    step_id="s1",
                    description="runtime create",
                    operation_type="create_object",
                    target_files=[],
                )
            ],
        )
        self.assertTrue(is_runtime_only_plan(plan))
        self.assertEqual(plan_mutation_files(plan), set())
        self.assertEqual(missing_required_obligations(plan, obs), [])

        v = PlanValidator(".")
        repo = RepoContext(file_tree=["forge/world.py", "forge/world/session.py"])
        plan_dict = {
            "goal": "create world object without source edits",
            "assumptions": [],
            "steps": [
                {
                    "step_id": "s1",
                    "description": "runtime create",
                    "operation_type": "create_object",
                    "target_files": [],
                }
            ],
        }
        accepted, _ = v.validate(plan_dict, repo, obligations=obs)
        self.assertEqual(accepted.steps[0].operation_type, "create_object")
        self.assertEqual(accepted.steps[0].target_files, [])

    def test_empty_modify_still_rejected_when_obligations_required(self):
        """Mutation plan must still cover obligations — no blanket exemption."""
        obs = self._req_obs()
        # In-memory: empty targets on modify → missing obligations
        plan = Plan(
            plan_id="p",
            goal="touch nothing",
            steps=[
                PlanStep(
                    step_id="s1",
                    description="bad",
                    operation_type="modify",
                    target_files=[],
                )
            ],
        )
        self.assertFalse(is_runtime_only_plan(plan))
        missing = missing_required_obligations(plan, obs)
        self.assertEqual(len(missing), 2)

        v = PlanValidator(".")
        repo = RepoContext(file_tree=["forge/world.py", "forge/world/session.py"])
        # Validator also rejects modify without target_files before obligations;
        # use a non-covering but non-empty target to isolate obligation check.
        plan_dict = {
            "goal": "change API",
            "assumptions": [],
            "steps": [
                {
                    "step_id": "s1",
                    "description": "modify unrelated",
                    "operation_type": "modify",
                    "target_files": ["forge/world.py"],
                    "start_line": 1,
                    "end_line": 1,
                    "new_text": "x = 1\n",
                }
            ],
        }
        # Only covers definition of World, not caller session.py → still missing
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "forge" / "world").mkdir(parents=True)
            (root / "forge" / "world.py").write_text("class World:\n    pass\n", encoding="utf-8")
            (root / "forge" / "world" / "session.py").write_text(
                "def f():\n    pass\n", encoding="utf-8"
            )
            v = PlanValidator(str(root))
            repo = RepoContext(
                file_tree=["forge/world.py", "forge/world/session.py"]
            )
            with self.assertRaises(PlanValidationError) as cm:
                v.validate(plan_dict, repo, obligations=obs)
            self.assertIn("obligation", str(cm.exception).lower())

    def test_mixed_create_object_plus_modify_still_requires_coverage(self):
        obs = self._req_obs()
        plan = Plan(
            plan_id="p",
            goal="mixed",
            steps=[
                PlanStep(
                    step_id="s0",
                    description="runtime",
                    operation_type="create_object",
                    target_files=[],
                ),
                PlanStep(
                    step_id="s1",
                    description="partial modify",
                    operation_type="modify",
                    target_files=["forge/world.py"],
                ),
            ],
        )
        self.assertFalse(is_runtime_only_plan(plan))
        self.assertEqual(plan_mutation_files(plan), {"forge/world.py"})
        missing = missing_required_obligations(plan, obs)
        self.assertTrue(any(m.get("file") == "forge/world/session.py" for m in missing))

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "forge" / "world").mkdir(parents=True)
            (root / "forge" / "world.py").write_text("class World:\n    pass\n", encoding="utf-8")
            (root / "forge" / "world" / "session.py").write_text(
                "def f():\n    pass\n", encoding="utf-8"
            )
            v = PlanValidator(str(root))
            repo = RepoContext(
                file_tree=["forge/world.py", "forge/world/session.py"]
            )
            plan_dict = {
                "goal": "mixed",
                "assumptions": [],
                "steps": [
                    {
                        "step_id": "s0",
                        "description": "runtime",
                        "operation_type": "create_object",
                        "target_files": [],
                    },
                    {
                        "step_id": "s1",
                        "description": "partial modify",
                        "operation_type": "modify",
                        "target_files": ["forge/world.py"],
                        "start_line": 1,
                        "end_line": 1,
                        "new_text": "class World:\n    pass\n",
                    },
                ],
            }
            with self.assertRaises(PlanValidationError) as cm:
                v.validate(plan_dict, repo, obligations=obs)
            self.assertIn("obligation", str(cm.exception).lower())
            self.assertIn("session", str(cm.exception))



if __name__ == "__main__":
    unittest.main()

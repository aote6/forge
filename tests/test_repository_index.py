"""Priority 2: Symbol + Reference Index regression tests."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from forge.context.index import RepositoryIndex, extract_focus_symbols
from forge.context.snapshot import take_snapshot
from forge.plan_validator import PlanValidator, PlanValidationError
from forge.protocols.models import Plan, PlanStep, RepoContext


def _w(root: Path, rel: str, content: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


FIXTURE_A = '''\
class Foo:
    def bar(self):
        return 1

def helper():
    return Foo()
'''

FIXTURE_B = '''\
from a import Foo

def use():
    x = Foo()
    return x
'''

FIXTURE_C = '''\
from a import Foo as F

def other():
    return F()
'''


class TestSymbolDefinitions(unittest.TestCase):
    def test_class_and_method_and_function(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _w(root, "a.py", FIXTURE_A)
            idx = RepositoryIndex.build(str(root))
            names = {s.qualified_name: s for s in idx.symbols}
            self.assertIn("Foo", names)
            self.assertEqual(names["Foo"].kind, "class")
            self.assertIn("Foo.bar", names)
            self.assertEqual(names["Foo.bar"].kind, "method")
            self.assertIn("helper", names)
            self.assertEqual(names["helper"].kind, "function")


class TestReferences(unittest.TestCase):
    def test_reference_to_imported_name(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _w(root, "a.py", FIXTURE_A)
            _w(root, "b.py", FIXTURE_B)
            idx = RepositoryIndex.build(str(root))
            refs = idx.find_references("Foo")
            files = {r.file_path for r in refs}
            self.assertIn("b.py", files)
            # definition site is Symbol, not required as Reference
            self.assertTrue(any(r.file_path == "b.py" and r.line > 0 for r in refs))

    def test_string_literal_not_reference(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _w(root, "s.py", 'x = "Foo"\ny = 1\n')
            idx = RepositoryIndex.build(str(root))
            refs = [r for r in idx.find_references("Foo") if r.file_path == "s.py"]
            self.assertEqual(refs, [], msg="string literal must not create Foo reference")


class TestImports(unittest.TestCase):
    def test_from_import_recorded(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _w(root, "a.py", FIXTURE_A)
            _w(root, "b.py", FIXTURE_B)
            idx = RepositoryIndex.build(str(root))
            imps = [i for i in idx.imports if i.file_path == "b.py"]
            self.assertTrue(any(i.is_from and "Foo" in i.names for i in imps))
            self.assertTrue(any(i.module == "a" for i in imps))


class TestReverseAndAffected(unittest.TestCase):
    def test_affected_files(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _w(root, "a.py", FIXTURE_A)
            _w(root, "b.py", FIXTURE_B)
            _w(root, "c.py", "from a import Foo\nz = Foo\n")
            idx = RepositoryIndex.build(str(root))
            aff = set(idx.affected_files("Foo"))
            self.assertIn("a.py", aff)
            self.assertIn("b.py", aff)
            self.assertIn("c.py", aff)


class TestSnapshotBinding(unittest.TestCase):
    def test_index_bound_to_snapshot(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _w(root, "a.py", "class A:\n    pass\n")
            snap = take_snapshot(str(root))
            idx = RepositoryIndex.build(str(root), snapshot=snap)
            self.assertEqual(idx.snapshot_id, snap.snapshot_id)
            _w(root, "a.py", "class A:\n    x = 1\n")
            snap2 = take_snapshot(str(root))
            self.assertNotEqual(snap.snapshot_id, snap2.snapshot_id)
            idx2 = RepositoryIndex.build(str(root), snapshot=snap2)
            self.assertEqual(idx2.snapshot_id, snap2.snapshot_id)
            self.assertNotEqual(idx.snapshot_id, idx2.snapshot_id)


class TestDeterministic(unittest.TestCase):
    def test_same_snapshot_same_index(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _w(root, "a.py", FIXTURE_A)
            _w(root, "b.py", FIXTURE_B)
            snap = take_snapshot(str(root))
            i1 = RepositoryIndex.build(str(root), snapshot=snap)
            i2 = RepositoryIndex.build(str(root), snapshot=snap)
            self.assertEqual(i1.snapshot_id, i2.snapshot_id)
            self.assertEqual(
                [(s.qualified_name, s.file_path, s.start_line) for s in i1.symbols],
                [(s.qualified_name, s.file_path, s.start_line) for s in i2.symbols],
            )
            self.assertEqual(
                [(r.symbol_name, r.file_path, r.line, r.kind) for r in i1.references],
                [(r.symbol_name, r.file_path, r.line, r.kind) for r in i2.references],
            )


class TestImpactValidation(unittest.TestCase):
    def test_target_outside_impact_rejected(self):
        v = PlanValidator(project_root=".")
        repo = RepoContext(file_tree=["a.py", "b.py", "c.py"])
        plan_dict = {
            "goal": "change Foo",
            "assumptions": [],
            "impact_files": ["a.py", "b.py"],
            "steps": [
                {
                    "step_id": "s1",
                    "description": "edit c",
                    "target_files": ["c.py"],
                    "operation_type": "modify",
                    "start_line": 1,
                    "end_line": 1,
                    "new_text": "x",
                }
            ],
        }
        # validator reads file for old_text — create temp
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _w(root, "a.py", "a\n")
            _w(root, "b.py", "b\n")
            _w(root, "c.py", "c\n")
            v = PlanValidator(str(root))
            repo = RepoContext(file_tree=["a.py", "b.py", "c.py"])
            with self.assertRaises(PlanValidationError) as cm:
                v.validate(plan_dict, repo)
            self.assertIn("impact_files", str(cm.exception))

    def test_target_inside_impact_ok(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _w(root, "a.py", "line1\n")
            _w(root, "b.py", "line1\n")
            v = PlanValidator(str(root))
            repo = RepoContext(file_tree=["a.py", "b.py"])
            plan_dict = {
                "goal": "change",
                "assumptions": [],
                "impact_files": ["a.py", "b.py"],
                "steps": [
                    {
                        "step_id": "s1",
                        "description": "edit a",
                        "target_files": ["a.py"],
                        "operation_type": "modify",
                        "start_line": 1,
                        "end_line": 1,
                        "new_text": "x\n",
                    }
                ],
            }
            plan, _ = v.validate(plan_dict, repo)
            self.assertEqual(plan.impact_files, ["a.py", "b.py"])

    def test_create_file_exempt_from_impact(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _w(root, "a.py", "a\n")
            v = PlanValidator(str(root))
            repo = RepoContext(file_tree=["a.py"])
            plan_dict = {
                "goal": "add",
                "assumptions": [],
                "impact_files": ["a.py"],
                "steps": [
                    {
                        "step_id": "s1",
                        "description": "new",
                        "target_files": ["new_mod.py"],
                        "operation_type": "create_file",
                        "content": "pass\n",
                    }
                ],
            }
            plan, _ = v.validate(plan_dict, repo)
            self.assertEqual(plan.steps[0].operation_type, "create_file")


class TestPlannerSummary(unittest.TestCase):
    def test_summary_not_raw_dump(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _w(root, "a.py", FIXTURE_A)
            idx = RepositoryIndex.build(str(root))
            text = idx.summary_for_planner(["Foo"])
            self.assertIn("symbol Foo", text)
            self.assertIn("affected_files", text)
            self.assertNotIn("RepositoryIndex(", text)


class TestOrchestratorBuildsIndex(unittest.TestCase):
    def test_understand_extra_has_index_summary(self):
        from unittest.mock import MagicMock
        from forge.orchestrator.engine import EngineeringOrchestrator
        from forge.memory.checkpoint import CheckpointStore
        from forge.protocols.models import OrchestratorPhase, TaskCheckpoint
        from forge.projections.base import ProjectionManager

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _w(root, "m.py", "class M:\n    pass\n")
            orch = EngineeringOrchestrator(
                project_root=str(root),
                world=MagicMock(),
                projections=ProjectionManager(checkpoint_dir=str(root / ".forge")),
                planner=MagicMock(),
                hub=MagicMock(),
                checkpoint_store=CheckpointStore(str(root)),
            )
            # avoid hub failure
            import forge.orchestrator.engine as eng
            orig = eng.get_repo_context
            eng.get_repo_context = lambda *a, **k: RepoContext(file_tree=["m.py"])
            try:
                orch.checkpoint = TaskCheckpoint(
                    task_id="t_idx",
                    phase=OrchestratorPhase.UNDERSTANDING.value,
                    goal="x",
                )
                orch.phase = OrchestratorPhase.UNDERSTANDING
                orch._step()
            finally:
                eng.get_repo_context = orig
            self.assertIn("repository_index", orch.checkpoint.extra)
            self.assertEqual(
                orch.checkpoint.extra["repository_index"]["snapshot_id"],
                orch.checkpoint.snapshot_id,
            )
            self.assertIsNotNone(orch._repository_index)
            self.assertGreaterEqual(orch._repository_index.symbol_count if False else len(orch._repository_index.symbols), 1)


if __name__ == "__main__":
    unittest.main()

"""Planner target source context injection (E2E integration fix)."""
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
    extract_task_paths,
    format_file_with_line_numbers,
    select_planning_content_files,
)
from forge.planner import Planner
from forge.protocols.models import RepoContext


def _w(root: Path, rel: str, content: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


class TestExtractTaskPaths(unittest.TestCase):
    def test_extracts_schemas_path(self):
        task = "修改 forge/tools/schemas.py 文件：删除 description 冗余"
        self.assertEqual(extract_task_paths(task), ["forge/tools/schemas.py"])


class TestSelectContentFiles(unittest.TestCase):
    def test_task_path_first(self):
        files = select_planning_content_files(
            task="edit forge/tools/schemas.py please",
            impact_files=["other.py"],
            obligations=[],
        )
        self.assertEqual(files[0], "forge/tools/schemas.py")
        self.assertIn("other.py", files)

    def test_does_not_include_whole_tree(self):
        tree = [f"f{i}.py" for i in range(200)]
        files = select_planning_content_files(
            task="edit core.py",
            impact_files=["core.py"],
            obligations=[],
            file_tree=tree,
            max_secondary=0,
        )
        self.assertEqual(files, ["core.py"])
        self.assertLess(len(files), 10)


class TestFormatLines(unittest.TestCase):
    def test_line_numbers(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _w(root, "a.py", "alpha\nbeta\ngamma\n")
            block = format_file_with_line_numbers(str(root), "a.py")
            self.assertIn("=== TARGET FILE: a.py", block)
            self.assertIn("0001  alpha", block)
            self.assertIn("0002  beta", block)
            self.assertIn("0003  gamma", block)

    def test_missing_file_empty(self):
        with tempfile.TemporaryDirectory() as td:
            block = format_file_with_line_numbers(td, "nope.py")
            self.assertEqual(block, "")


class TestPlannerInjection(unittest.TestCase):
    def test_target_source_in_prompt(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            body = (
                "MUTATION_TOOL_DECLARATIONS = [\n"
                '    {"description": "创建一个新文件。仅可通过 EngineeringOrchestrator 执行，不可在 tool-loop 中调用。"},\n'
                "]\n"
            )
            _w(root, "forge/tools/schemas.py", body)
            _w(root, "unrelated_noise.py", "x = 1\n" * 5000)
            # many decoy files that would exhaust old budget
            for i in range(50):
                _w(root, f"decoy_{i:02d}.py", f"# decoy {i}\n" + ("pass\n" * 200))

            captured = {}

            def send(messages, tools=None):
                captured["messages"] = messages
                # minimal valid plan targeting schemas
                plan = {
                    "goal": "trim descriptions",
                    "impact_files": ["forge/tools/schemas.py"],
                    "steps": [
                        {
                            "step_id": "s1",
                            "description": "fix description",
                            "target_files": ["forge/tools/schemas.py"],
                            "operation_type": "modify",
                            "start_line": 2,
                            "end_line": 2,
                            "new_text": '    {"description": "创建一个新文件。"},\n',
                        }
                    ],
                }
                return SimpleNamespace(content=json.dumps(plan))

            adapter = MagicMock()
            adapter.send = MagicMock(side_effect=send)
            planner = Planner(adapter)
            tree = ["forge/tools/schemas.py", "unrelated_noise.py"] + [
                f"decoy_{i:02d}.py" for i in range(50)
            ]
            repo = RepoContext(file_tree=tree)
            idx = RepositoryIndex.build(str(root))
            task = (
                "修改 forge/tools/schemas.py 文件：删除 MUTATION_TOOL_DECLARATIONS "
                "中 description 的冗余提示文字"
            )
            plan, enriched = planner.plan(
                task, repo, project_root=str(root), index=idx
            )
            user = captured["messages"][1].content
            self.assertIn("forge/tools/schemas.py", user)
            self.assertIn("TARGET FILE: forge/tools/schemas.py", user)
            self.assertIn("MUTATION_TOOL_DECLARATIONS", user)
            self.assertIn("仅可通过 EngineeringOrchestrator 执行", user)
            # line numbers present
            self.assertRegex(user, r"0001\s+")
            # unrelated huge file must NOT be fully injected
            self.assertNotIn("TARGET FILE: unrelated_noise.py", user)
            self.assertNotIn("TARGET FILE: decoy_00.py", user)
            self.assertIn("forge/tools/schemas.py", enriched["injected_source_files"])
            # prompt should not balloon to whole-repo content
            self.assertLess(len(user), 100_000)

    def test_create_file_missing_ok(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _w(root, "a.py", "x=1\n")
            captured = {}

            def send(messages, tools=None):
                captured["messages"] = messages
                return SimpleNamespace(
                    content=json.dumps(
                        {
                            "goal": "add",
                            "steps": [
                                {
                                    "step_id": "s1",
                                    "description": "create",
                                    "target_files": ["brand_new.py"],
                                    "operation_type": "create_file",
                                    "content": "y=2\n",
                                }
                            ],
                        }
                    )
                )

            adapter = MagicMock(send=MagicMock(side_effect=send))
            planner = Planner(adapter)
            repo = RepoContext(file_tree=["a.py"])
            plan, _ = planner.plan(
                "创建 brand_new.py 模块",
                repo,
                project_root=str(root),
                index=RepositoryIndex.build(str(root)),
            )
            user = captured["messages"][1].content
            self.assertIn("brand_new.py", user)
            self.assertIn("not present on disk", user)
            self.assertEqual(plan.steps[0].operation_type, "create_file")


if __name__ == "__main__":
    unittest.main()

"""Regression: process-local Snapshot + RepositoryIndex caches.

Hard requirements:
- same tree_hash → reuse Snapshot object (after scan that yields tree_hash)
- same snapshot_id → reuse RepositoryIndex object; no additional ast.parse
- different snapshot_id → no false cache hit
"""
from __future__ import annotations

import ast
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from forge.context import snapshot as snapshot_mod
from forge.context import index as index_mod
from forge.context.snapshot import take_snapshot, RepositorySnapshot
from forge.context.index import RepositoryIndex
from forge.context.scanner import scan_files


def _write(repo: Path, rel: str, content: str) -> None:
    p = repo / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


def _clear_caches() -> None:
    snapshot_mod._snapshot_cache.clear()
    index_mod._index_cache.clear()


class TestSnapshotCache(unittest.TestCase):
    def setUp(self) -> None:
        _clear_caches()

    def tearDown(self) -> None:
        _clear_caches()

    def test_second_call_same_snapshot_id_and_identity(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(root, "a.py", "x = 1\n")
            s1 = take_snapshot(str(root))
            s2 = take_snapshot(str(root))
            self.assertEqual(s1.snapshot_id, s2.snapshot_id)
            self.assertIs(s1, s2)
            self.assertEqual(len(snapshot_mod._snapshot_cache), 1)

    def test_snapshot_object_built_once_for_same_tree_hash(self):
        """tree_hash still requires scan; we only cache the Snapshot object."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(root, "a.py", "x = 1\n")
            build_calls = {"n": 0}
            real_build = snapshot_mod.build_context

            def counting_build(*args, **kwargs):
                build_calls["n"] += 1
                return real_build(*args, **kwargs)

            with patch.object(snapshot_mod, "build_context", side_effect=counting_build):
                s1 = take_snapshot(str(root))
                s2 = take_snapshot(str(root))
            # Both calls still scan (build_context) to obtain tree_hash
            self.assertEqual(build_calls["n"], 2)
            self.assertIs(s1, s2)
            self.assertEqual(s1.snapshot_id, s2.snapshot_id)

    def test_different_content_different_snapshot(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(root, "a.py", "x = 1\n")
            s1 = take_snapshot(str(root))
            _write(root, "a.py", "x = 2\n")
            s2 = take_snapshot(str(root))
            self.assertNotEqual(s1.snapshot_id, s2.snapshot_id)
            self.assertIsNot(s1, s2)
            self.assertEqual(len(snapshot_mod._snapshot_cache), 2)


class TestRepositoryIndexCache(unittest.TestCase):
    def setUp(self) -> None:
        _clear_caches()

    def tearDown(self) -> None:
        _clear_caches()

    def test_same_snapshot_returns_same_index_identity(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(root, "a.py", "def foo():\n    return 1\n")
            snap = take_snapshot(str(root))
            idx1 = RepositoryIndex.build(str(root), snapshot=snap)
            idx2 = RepositoryIndex.build(str(root), snapshot=snap)
            self.assertIs(idx1, idx2)
            self.assertEqual(idx1.snapshot_id, snap.snapshot_id)

    def test_second_build_does_not_call_ast_parse(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(root, "a.py", "def foo():\n    return 1\n")
            _write(root, "b.py", "def bar():\n    return 2\n")
            snap = take_snapshot(str(root))

            parse_count = {"n": 0}
            real_parse = ast.parse

            def counting_parse(*args, **kwargs):
                parse_count["n"] += 1
                return real_parse(*args, **kwargs)

            with patch("forge.context.index.ast.parse", side_effect=counting_parse):
                idx1 = RepositoryIndex.build(str(root), snapshot=snap)
                n_first = parse_count["n"]
                self.assertGreater(n_first, 0)
                idx2 = RepositoryIndex.build(str(root), snapshot=snap)
                n_second = parse_count["n"]
            self.assertEqual(n_second, n_first, "second build must not call ast.parse again")
            self.assertIs(idx1, idx2)

    def test_different_snapshot_no_false_hit(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(root, "a.py", "def foo():\n    return 1\n")
            snap_a = take_snapshot(str(root))
            idx_a = RepositoryIndex.build(str(root), snapshot=snap_a)

            _write(root, "a.py", "def foo():\n    return 2\n")
            snap_b = take_snapshot(str(root))
            self.assertNotEqual(snap_a.snapshot_id, snap_b.snapshot_id)

            idx_b = RepositoryIndex.build(str(root), snapshot=snap_b)
            self.assertIsNot(idx_a, idx_b)
            self.assertNotEqual(idx_a.snapshot_id, idx_b.snapshot_id)
            # _index_cache is now keyed by repo_path; both snapshots live under
            # the same repo key. Assert the repo has 2 snapshot entries instead.
            repo_key = next(iter(index_mod._index_cache))
            self.assertEqual(len(index_mod._index_cache[repo_key]), 2)

    def test_build_without_explicit_snapshot_still_caches(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _write(root, "m.py", "class M:\n    pass\n")
            idx1 = RepositoryIndex.build(str(root))
            idx2 = RepositoryIndex.build(str(root))
            self.assertIs(idx1, idx2)


if __name__ == "__main__":
    unittest.main()

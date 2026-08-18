"""P2: file-level incremental RepositoryIndex rebuild."""
from __future__ import annotations

import ast
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import forge.context.index as index_mod
from forge.context.index import RepositoryIndex


def _reset_caches():
    index_mod._index_cache.clear()
    index_mod._repo_last_index.clear()
    index_mod._repo_last_fingerprints.clear()


def _write(root: Path, rel: str, content: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


class TestIncrementalIndex(unittest.TestCase):
    def setUp(self):
        _reset_caches()
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        _write(self.root, "a.py", "def foo():\n    pass\n")
        _write(self.root, "b.py", "def bar():\n    pass\n")

    def tearDown(self):
        self._tmp.cleanup()
        _reset_caches()

    def _build_with_parse_count(self):
        with patch("forge.context.index.ast.parse", wraps=ast.parse) as mock_parse:
            idx = RepositoryIndex.build(str(self.root))
        return idx, mock_parse.call_count

    def test_full_build_then_incremental_zero_parse_for_unchanged(self):
        idx1, count1 = self._build_with_parse_count()
        self.assertEqual(count1, 2)
        self.assertEqual(len(idx1.files_indexed), 2)

        # No change → new snapshot_id but all fingerprints same → 0 parse
        idx2, count2 = self._build_with_parse_count()
        self.assertEqual(count2, 0)
        # Equivalent to full build
        self.assertEqual(
            sorted(s.qualified_name for s in idx2.symbols),
            sorted(s.qualified_name for s in idx1.symbols),
        )

    def test_single_file_change_only_reparses_one(self):
        self._build_with_parse_count()

        _write(self.root, "a.py", "def foo():\n    return 42\n")
        idx, count = self._build_with_parse_count()
        self.assertEqual(count, 1)
        # b.py unchanged, still indexed
        self.assertIn("b.py", idx.files_indexed)
        # a.py new content reflected
        a_symbols = [s for s in idx.symbols if s.file_path == "a.py"]
        self.assertEqual(len(a_symbols), 1)
        self.assertEqual(a_symbols[0].name, "foo")

    def test_deleted_file_symbols_removed(self):
        idx1, _ = self._build_with_parse_count()
        self.assertEqual(len(idx1.symbols), 2)

        (self.root / "b.py").unlink()
        idx2, count = self._build_with_parse_count()
        self.assertEqual(count, 0)  # only deletion, no parse
        self.assertNotIn("b.py", idx2.files_indexed)
        self.assertTrue(all(s.file_path != "b.py" for s in idx2.symbols))
        self.assertTrue(all(r.file_path != "b.py" for r in idx2.references))

    def test_added_file_symbols_present(self):
        self._build_with_parse_count()

        _write(self.root, "c.py", "def baz():\n    pass\n")
        idx, count = self._build_with_parse_count()
        self.assertEqual(count, 1)
        self.assertIn("c.py", idx.files_indexed)
        self.assertTrue(any(s.file_path == "c.py" and s.name == "baz" for s in idx.symbols))

    def test_incremental_equivalent_to_full(self):
        idx_full, _ = self._build_with_parse_count()

        # Force a fresh full build by clearing all state
        _reset_caches()
        idx_full2, _ = self._build_with_parse_count()

        _write(self.root, "a.py", "def foo():\n    x = 1\n    return x\n")
        (self.root / "b.py").unlink()
        _write(self.root, "c.py", "class C:\n    pass\n")

        # Incremental path
        idx_inc, _ = self._build_with_parse_count()

        # Full path for comparison
        _reset_caches()
        idx_full3, _ = self._build_with_parse_count()

        self.assertEqual(
            sorted((s.qualified_name, s.file_path, s.start_line) for s in idx_inc.symbols),
            sorted((s.qualified_name, s.file_path, s.start_line) for s in idx_full3.symbols),
        )
        self.assertEqual(
            sorted((r.symbol_name, r.file_path, r.line) for r in idx_inc.references),
            sorted((r.symbol_name, r.file_path, r.line) for r in idx_full3.references),
        )
        self.assertEqual(sorted(idx_inc.files_indexed), sorted(idx_full3.files_indexed))


if __name__ == "__main__":
    unittest.main()

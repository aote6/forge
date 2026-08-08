"""Machine verification target selection (Priority 8).

From RepositoryIndex + obligations + impact, derive the minimal set of
related tests. Deterministic, no LLM.
"""
from __future__ import annotations

import os
from typing import Any, Optional


def _is_test_path(path: str) -> bool:
    p = (path or "").replace("\\", "/").lower()
    base = p.rsplit("/", 1)[-1]
    return (
        "/tests/" in f"/{p}"
        or p.startswith("tests/")
        or base.startswith("test_")
        or base.endswith("_test.py")
    )


def _module_stem(path: str) -> str:
    base = path.replace("\\", "/").rsplit("/", 1)[-1]
    if base.endswith(".py"):
        base = base[:-3]
    return base


def _same_name_test_candidates(src_file: str, indexed_files: list[str]) -> list[str]:
    """Advisory: foo.py ↔ tests/test_foo.py style mapping."""
    stem = _module_stem(src_file)
    if not stem or stem.startswith("test_"):
        return []
    candidates = []
    patterns = {
        f"tests/test_{stem}.py",
        f"test_{stem}.py",
        f"tests/{stem}_test.py",
        f"{stem}_test.py",
    }
    for f in indexed_files or []:
        norm = f.replace("\\", "/")
        if norm in patterns or norm.endswith(f"/test_{stem}.py"):
            candidates.append(f)
    return sorted(set(candidates))


def select_verification_targets(
    index=None,
    *,
    obligations: Optional[list[dict[str, Any]]] = None,
    impact_files: Optional[list[str]] = None,
    failed_tests: Optional[list[str]] = None,
    project_root: str = ".",
) -> dict[str, Any]:
    """Select related tests for VERIFY.

    Returns:
      {
        "test_files": sorted unique files to run (required + advisory + forced),
        "test_symbols": sorted symbol names related to selection,
        "required": [{file, reason, rule}, ...],
        "advisory": [{file, reason, rule}, ...],
        "forced_failed": [file or node ids from failure history],
        "reasons": {file: [reason, ...]},
        "empty": bool,  # True when machine found nothing (caller may SMS fallback)
      }
    """
    obligations = list(obligations or [])
    impact_files = list(impact_files or [])
    failed_tests = list(failed_tests or [])

    required: dict[str, list[str]] = {}  # file -> reasons
    advisory: dict[str, list[str]] = {}
    test_symbols: set[str] = set()

    indexed_files: list[str] = []
    if index is not None:
        indexed_files = list(getattr(index, "files_indexed", None) or [])

    # Obligation symbols
    obl_symbols: set[str] = set()
    obl_files: set[str] = set()
    for o in obligations:
        if o.get("symbol"):
            obl_symbols.add(o["symbol"])
        if o.get("file"):
            obl_files.add(o["file"])
        if o.get("required") and o.get("symbol"):
            test_symbols.add(o["symbol"])

    impact_set = set(impact_files) | obl_files

    # --- Rule 1: Direct reference from test files to obligation symbols ---
    if index is not None and obl_symbols:
        for name in sorted(obl_symbols):
            for r in index.find_references(name):
                if _is_test_path(r.file_path):
                    required.setdefault(r.file_path, []).append(
                        f"direct_ref:{name}@{r.file_path}:{r.line}"
                    )
                    test_symbols.add(name)
            for imp in getattr(index, "imports", None) or []:
                if name in (imp.names or ()) and _is_test_path(imp.file_path):
                    required.setdefault(imp.file_path, []).append(
                        f"direct_import:{name}@{imp.file_path}"
                    )
                    test_symbols.add(name)

    # --- Rule 2: Module importer — test imports a module under impact ---
    if index is not None and impact_set:
        # map path stem / dotted module fragments to impact files
        impact_stems = {_module_stem(f): f for f in impact_set if f.endswith(".py")}
        for imp in getattr(index, "imports", None) or []:
            if not _is_test_path(imp.file_path):
                continue
            mod = (imp.module or "").replace(".", "/")
            # match last component of import module to impact stem
            parts = [p for p in (imp.module or "").split(".") if p]
            hit = False
            for part in parts:
                if part in impact_stems:
                    required.setdefault(imp.file_path, []).append(
                        f"module_import:{imp.module}->impact:{impact_stems[part]}"
                    )
                    hit = True
                    break
            if not hit and mod:
                for f in impact_set:
                    norm = f.replace("\\", "/").removesuffix(".py")
                    if norm.endswith(mod) or mod.endswith(norm.split("/")[-1]):
                        required.setdefault(imp.file_path, []).append(
                            f"module_import:{imp.module}->impact:{f}"
                        )
                        break

    # --- Rule 3: Same-name convention (advisory) ---
    for src in sorted(impact_set):
        if not src.endswith(".py") or _is_test_path(src):
            continue
        for cand in _same_name_test_candidates(src, indexed_files):
            if cand in required:
                continue
            # also accept if file exists on disk even if not indexed
            full = os.path.join(project_root, cand)
            if cand in indexed_files or os.path.isfile(full):
                advisory.setdefault(cand, []).append(f"same_name:{src}->{cand}")

    # --- Rule 4: Previously failed tests (forced) ---
    forced: list[str] = []
    for ft in failed_tests:
        if not ft:
            continue
        # may be "tests/test_x.py::test_y" or plain path
        file_part = ft.split("::", 1)[0].strip()
        forced.append(ft)
        if file_part.endswith(".py"):
            required.setdefault(file_part, []).append(f"prior_failure:{ft}")
        else:
            # bare node id — keep in forced list for SMS payload
            required.setdefault(file_part, []).append(f"prior_failure:{ft}")

    # Build stable output
    req_list = [
        {"file": f, "reason": reasons[0] if reasons else "", "rule": reasons[0].split(":")[0] if reasons else "", "reasons": reasons}
        for f, reasons in sorted(required.items())
    ]
    adv_list = [
        {"file": f, "reason": reasons[0] if reasons else "", "rule": "same_name", "reasons": reasons}
        for f, reasons in sorted(advisory.items())
        if f not in required
    ]

    test_files = sorted(set(required.keys()) | set(advisory.keys()))
    reasons_map = {f: required.get(f) or advisory.get(f) or [] for f in test_files}

    return {
        "test_files": test_files,
        "test_symbols": sorted(test_symbols),
        "required": req_list,
        "advisory": adv_list,
        "forced_failed": sorted(set(forced)),
        "reasons": reasons_map,
        "empty": len(test_files) == 0 and len(forced) == 0,
    }


def extract_failed_tests_from_history(failure_history: list) -> list[str]:
    """Collect failed test ids/files from P3 failure_history entries."""
    out: list[str] = []
    for entry in failure_history or []:
        if not isinstance(entry, dict):
            continue
        ev = entry.get("evidence") or {}
        for key in ("failed_tests",):
            for t in ev.get(key) or []:
                if t and str(t) not in out:
                    out.append(str(t))
        be = ev.get("build_evidence") or {}
        for t in be.get("failed_tests") or []:
            if t and str(t) not in out:
                out.append(str(t))
        for f in entry.get("files") or []:
            if _is_test_path(str(f)) and str(f) not in out:
                out.append(str(f))
    return out

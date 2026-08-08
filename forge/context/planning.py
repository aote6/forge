"""Machine planning support — impact set + step dependency ordering.

Priority 4: planning precision without relying on LLM guesswork for
impact boundaries or execution order. Index queries remain the source
of truth; unresolved relations stay unresolved.
"""
from __future__ import annotations

from typing import Any, Iterable, Optional


def compute_impact_set(
    index,
    task: str = "",
    focus_symbols: Optional[list[str]] = None,
    seed_files: Optional[list[str]] = None,
) -> dict[str, Any]:
    """Derive a machine impact set from RepositoryIndex.

    Returns:
      {
        "impact_files": sorted list[str],
        "impact_symbols": list[str] used,
        "ambiguous_symbols": {name: [file_paths]},
        "callers_by_symbol": {name: [file_paths]},
        "definitions_by_symbol": {name: [file_paths]},
      }
    """
    from forge.context.index import extract_focus_symbols

    symbols = list(focus_symbols or [])
    if not symbols and task:
        symbols = extract_focus_symbols(task)

    impact_files: set[str] = set(seed_files or [])
    used: list[str] = []
    ambiguous: dict[str, list[str]] = {}
    callers: dict[str, list[str]] = {}
    definitions: dict[str, list[str]] = {}

    if index is None:
        return {
            "impact_files": sorted(impact_files),
            "impact_symbols": used,
            "ambiguous_symbols": ambiguous,
            "callers_by_symbol": callers,
            "definitions_by_symbol": definitions,
        }

    for name in symbols:
        defs = index.find_definition(name)
        refs = index.find_references(name)
        # Import sites that bind the name (from X import name)
        import_files: list[str] = []
        for imp in getattr(index, "imports", None) or []:
            if name in (imp.names or ()):
                import_files.append(imp.file_path)
        if not defs and not refs and not import_files:
            continue
        used.append(name)
        def_files = sorted({d.file_path for d in defs})
        definitions[name] = def_files
        # Multiple definition sites for the same short name → ambiguous
        if len(def_files) > 1:
            ambiguous[name] = def_files
        ref_files = sorted({r.file_path for r in refs} | set(import_files))
        # Keep all ref/import files as callers for planning visibility
        callers[name] = sorted(set(ref_files))
        for d in defs:
            impact_files.add(d.file_path)
        for r in refs:
            impact_files.add(r.file_path)
        for f in import_files:
            impact_files.add(f)

    return {
        "impact_files": sorted(impact_files),
        "impact_symbols": used,
        "ambiguous_symbols": ambiguous,
        "callers_by_symbol": callers,
        "definitions_by_symbol": definitions,
    }


def merge_impact(
    machine: dict[str, Any],
    plan_impact_files: Optional[list[str]] = None,
    plan_impact_symbols: Optional[list[str]] = None,
) -> tuple[list[str], list[str]]:
    """Union machine impact with any LLM-declared impact (machine always included)."""
    files: set[str] = set(machine.get("impact_files") or [])
    for f in plan_impact_files or []:
        if f:
            files.add(f)
    symbols: list[str] = []
    seen = set()
    for s in list(machine.get("impact_symbols") or []) + list(plan_impact_symbols or []):
        if s and s not in seen:
            seen.add(s)
            symbols.append(s)
    return sorted(files), symbols


def prioritize_content_files(
    file_tree: list[str],
    impact_files: list[str],
    changed_files: Optional[list[str]] = None,
) -> list[str]:
    """Order content candidates: impact first, then changed, then rest (stable)."""
    impact = list(impact_files or [])
    changed = list(changed_files or [])
    rest = [f for f in (file_tree or []) if f not in set(impact) | set(changed)]
    # preserve relative order within each bucket via dict.fromkeys
    return list(dict.fromkeys(impact + changed + rest))


def format_impact_section(machine: dict[str, Any]) -> str:
    """Structured text block for planner prompt (machine facts)."""
    lines = [
        "Machine impact set (from RepositoryIndex — required consideration):",
        f"  impact_files: {machine.get('impact_files') or []}",
        f"  impact_symbols: {machine.get('impact_symbols') or []}",
    ]
    amb = machine.get("ambiguous_symbols") or {}
    if amb:
        lines.append("  AMBIGUOUS symbols (do not assume a single definition):")
        for name, files in sorted(amb.items()):
            lines.append(f"    {name}: definitions in {files}")
    callers = machine.get("callers_by_symbol") or {}
    for name, files in sorted(callers.items()):
        lines.append(f"  callers of {name}: {files}")
    defs = machine.get("definitions_by_symbol") or {}
    for name, files in sorted(defs.items()):
        lines.append(f"  definition of {name}: {files}")
    return "\n".join(lines)


def topological_order_steps(steps: list) -> list:
    """Reorder steps so dependencies appear before dependents.

    Unknown / missing deps are tolerated (already validated elsewhere).
    Cycles: best-effort stable order of remaining nodes.
    """
    if not steps:
        return steps
    by_id = {}
    for s in steps:
        sid = getattr(s, "step_id", None) or ""
        by_id[sid] = s
    indeg = {sid: 0 for sid in by_id}
    graph: dict[str, list[str]] = {sid: [] for sid in by_id}
    for s in steps:
        sid = getattr(s, "step_id", "") or ""
        for dep in getattr(s, "dependencies", None) or []:
            if dep in by_id and dep != sid:
                graph[dep].append(sid)
                indeg[sid] = indeg.get(sid, 0) + 1
    # Kahn with stable queue ordered by original appearance
    order_index = {getattr(s, "step_id", ""): i for i, s in enumerate(steps)}
    ready = sorted(
        [sid for sid, d in indeg.items() if d == 0],
        key=lambda x: order_index.get(x, 10**9),
    )
    ordered: list = []
    seen = set()
    while ready:
        sid = ready.pop(0)
        if sid in seen:
            continue
        seen.add(sid)
        ordered.append(by_id[sid])
        for nxt in graph.get(sid, []):
            indeg[nxt] -= 1
            if indeg[nxt] == 0:
                ready.append(nxt)
                ready.sort(key=lambda x: order_index.get(x, 10**9))
    # append any residual (cycle) in original order
    for s in steps:
        sid = getattr(s, "step_id", "") or ""
        if sid not in seen:
            ordered.append(s)
    return ordered


def apply_machine_impact_to_plan(plan, machine: dict[str, Any]) -> None:
    """Mutate plan: merge impact sets and topologically order steps."""
    files, symbols = merge_impact(
        machine,
        getattr(plan, "impact_files", None),
        getattr(plan, "impact_symbols", None),
    )
    if files:
        plan.impact_files = files
    if symbols:
        plan.impact_symbols = symbols
    if getattr(plan, "steps", None):
        plan.steps = topological_order_steps(list(plan.steps))


def explain_why_file_in_impact(machine: dict[str, Any], path: str) -> list[str]:
    """Human/machine-readable reasons a path entered the impact set."""
    reasons = []
    for name, files in (machine.get("definitions_by_symbol") or {}).items():
        if path in files:
            reasons.append(f"defines symbol '{name}'")
    for name, files in (machine.get("callers_by_symbol") or {}).items():
        if path in files:
            reasons.append(f"references symbol '{name}'")
    if not reasons and path in (machine.get("impact_files") or []):
        reasons.append("seeded or merged into impact set")
    return reasons


def derive_expected_symbols_for_plan(plan, index) -> None:
    """Fill PlanStep.expected_symbols from P2 Index (machine, not LLM).

    modify: union of Symbol.qualified_name defined in target_files (deterministic).
    create_file / delete: empty list.
    Mutates plan steps in place. Index must match plan's snapshot binding.
    """
    if plan is None or index is None:
        return
    # Index symbols already ordered; group by file once
    by_file: dict[str, list[str]] = {}
    for sym in getattr(index, "symbols", None) or []:
        by_file.setdefault(sym.file_path, []).append(sym.qualified_name)

    for step in getattr(plan, "steps", None) or []:
        op = getattr(step, "operation_type", "modify") or "modify"
        if op in ("create_file", "delete_file", "delete"):
            step.expected_symbols = []
            continue
        names: set[str] = set()
        for f in getattr(step, "target_files", None) or []:
            for q in by_file.get(f, []):
                names.add(q)
        step.expected_symbols = sorted(names)


def plan_expected_symbols_map(plan) -> dict:
    """Aggregate step.expected_symbols into {file_path: [qualified_names]} for VERIFY."""
    out: dict[str, list[str]] = {}
    if plan is None:
        return out
    for step in getattr(plan, "steps", None) or []:
        op = getattr(step, "operation_type", "") or ""
        if op in ("create_file", "delete_file", "delete"):
            continue
        syms = list(getattr(step, "expected_symbols", None) or [])
        if not syms:
            continue
        for f in getattr(step, "target_files", None) or []:
            bucket = out.setdefault(f, [])
            for s in syms:
                if s not in bucket:
                    bucket.append(s)
    for f in out:
        out[f] = sorted(set(out[f]))
    return out


def content_hashes(project_root: str, files: list[str]) -> dict[str, str]:
    """SHA-256 of file contents; missing files map to empty string."""
    import hashlib
    import os

    result: dict[str, str] = {}
    for rel in files or []:
        full = os.path.join(project_root, rel)
        if not os.path.isfile(full):
            result[rel] = ""
            continue
        try:
            with open(full, "rb") as fh:
                data = fh.read()
            result[rel] = hashlib.sha256(data).hexdigest()
        except OSError:
            result[rel] = ""
    return result


def collect_plan_target_files(plan) -> list[str]:
    files: list[str] = []
    if plan is None:
        return files
    for s in getattr(plan, "steps", None) or []:
        files.extend(getattr(s, "target_files", None) or [])
    return list(dict.fromkeys(files))


def _is_test_path(path: str) -> bool:
    p = (path or "").replace("\\", "/").lower()
    base = p.rsplit("/", 1)[-1]
    return (
        "/tests/" in f"/{p}"
        or p.startswith("tests/")
        or base.startswith("test_")
        or base.endswith("_test.py")
    )


def compute_obligations(
    index,
    task: str = "",
    focus_symbols: Optional[list[str]] = None,
    machine: Optional[dict[str, Any]] = None,
    repair_constraints=None,
) -> list[dict[str, Any]]:
    """Derive mutation obligations from Index (P7).

    Roles:
      definition  — unique definition site of a focus symbol (required)
      caller      — non-definition file with Name/Attribute reference (required)
      import_site — file that only imports the name, no other refs (advisory)
      test        — test-path reference/import sites (advisory by default)

    Ambiguous multi-definition symbols produce advisory-only rows
    (required=False) so Validator does not force a wrong file.

    create_file / no index hits → empty list.
    """
    from forge.context.index import extract_focus_symbols

    if machine is None:
        machine = compute_impact_set(index, task=task, focus_symbols=focus_symbols)
    symbols = list(focus_symbols or machine.get("impact_symbols") or [])
    if not symbols and task:
        symbols = extract_focus_symbols(task)

    obligations: list[dict[str, Any]] = []
    if index is None or not symbols:
        return obligations

    ambiguous = dict(machine.get("ambiguous_symbols") or {})
    definitions = dict(machine.get("definitions_by_symbol") or {})
    callers_map = dict(machine.get("callers_by_symbol") or {})

    for name in symbols:
        def_files = list(definitions.get(name) or [])
        if not def_files:
            # try live index
            def_files = sorted({d.file_path for d in index.find_definition(name)})
        is_ambiguous = name in ambiguous or len(def_files) > 1

        if is_ambiguous:
            for f in sorted(set(def_files)):
                obligations.append(
                    {
                        "file": f,
                        "symbol": name,
                        "role": "definition",
                        "required": False,
                        "reason": f"ambiguous definition of '{name}' in multiple files",
                        "ambiguous": True,
                    }
                )
            continue

        if not def_files:
            continue

        def_file = def_files[0]
        obligations.append(
            {
                "file": def_file,
                "symbol": name,
                "role": "definition",
                "required": True,
                "reason": f"unique definition of '{name}'",
                "ambiguous": False,
            }
        )

        # Reference files (Name/Attribute) excluding definition
        ref_files: set[str] = set()
        for r in index.find_references(name):
            if r.file_path != def_file:
                ref_files.add(r.file_path)

        # Import-only sites
        import_files: set[str] = set()
        for imp in getattr(index, "imports", None) or []:
            if name in (imp.names or ()) and imp.file_path != def_file:
                import_files.add(imp.file_path)

        for f in sorted(ref_files):
            if _is_test_path(f):
                obligations.append(
                    {
                        "file": f,
                        "symbol": name,
                        "role": "test",
                        "required": False,
                        "reason": f"test path references '{name}' (advisory)",
                        "ambiguous": False,
                    }
                )
            else:
                obligations.append(
                    {
                        "file": f,
                        "symbol": name,
                        "role": "caller",
                        "required": True,
                        "reason": f"code reference to '{name}'",
                        "ambiguous": False,
                    }
                )

        for f in sorted(import_files - ref_files):
            # import without other refs in that file
            if _is_test_path(f):
                obligations.append(
                    {
                        "file": f,
                        "symbol": name,
                        "role": "test",
                        "required": False,
                        "reason": f"test import of '{name}' (advisory)",
                        "ambiguous": False,
                    }
                )
            else:
                obligations.append(
                    {
                        "file": f,
                        "symbol": name,
                        "role": "import_site",
                        "required": False,
                        "reason": f"import site of '{name}' without local refs (advisory)",
                        "ambiguous": False,
                    }
                )

    # Intersect with repair constraints (fail-closed narrowing)
    if repair_constraints is not None:
        rc = repair_constraints
        if isinstance(rc, dict):
            from forge.failures import RepairConstraints

            rc = RepairConstraints.from_dict(rc)
        allowed: set[str] | None = None
        must = set(getattr(rc, "must_touch_files", None) or [])
        req_imp = set(getattr(rc, "required_impact_files", None) or [])
        force = set(getattr(rc, "force_create_files", None) or [])
        if must or req_imp or force:
            allowed = must | req_imp | force
        if allowed is not None:
            narrowed: list[dict[str, Any]] = []
            for o in obligations:
                if o.get("required") and o.get("file") not in allowed:
                    # demote rather than force conflict: required outside repair
                    # becomes advisory with conflict reason; coverage uses required only
                    o = dict(o)
                    o["required"] = False
                    o["reason"] = (
                        o.get("reason", "")
                        + f" [demoted: outside repair_constraints {sorted(allowed)}]"
                    )
                    o["repair_conflict"] = True
                narrowed.append(o)
            obligations = narrowed

    # deterministic order
    obligations.sort(
        key=lambda o: (
            0 if o.get("required") else 1,
            o.get("role") or "",
            o.get("file") or "",
            o.get("symbol") or "",
        )
    )
    return obligations


def required_obligation_files(obligations: list[dict[str, Any]]) -> list[str]:
    files = []
    seen = set()
    for o in obligations or []:
        if not o.get("required"):
            continue
        f = o.get("file")
        if f and f not in seen:
            seen.add(f)
            files.append(f)
    return files


def plan_mutation_files(plan) -> set[str]:
    """Files the plan actually mutates (modify/create/delete targets)."""
    out: set[str] = set()
    if plan is None:
        return out
    for s in getattr(plan, "steps", None) or []:
        for f in getattr(s, "target_files", None) or []:
            out.add(f)
    return out


def missing_required_obligations(
    plan,
    obligations: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Required obligations whose file is not among plan mutation targets."""
    covered = plan_mutation_files(plan)
    missing = []
    for o in obligations or []:
        if not o.get("required"):
            continue
        if o.get("file") not in covered:
            missing.append(o)
    return missing


def format_obligations_section(obligations: list[dict[str, Any]]) -> str:
    if not obligations:
        return "Machine obligations: (none)"
    lines = ["Machine obligations (required must appear as plan targets):"]
    for o in obligations:
        tag = "REQUIRED" if o.get("required") else "advisory"
        lines.append(
            f"  [{tag}] {o.get('role')} {o.get('symbol')} @ {o.get('file')}"
            f" — {o.get('reason')}"
        )
    return "\n".join(lines)

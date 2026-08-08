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

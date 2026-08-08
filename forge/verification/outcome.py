"""Plan / filesystem outcome verification — machine facts only.

Priority 5: after EXECUTE, prove mutations matched the plan and
changed Python files remain syntactically valid. No LLM claims.
"""
from __future__ import annotations

import ast
import os
from dataclasses import asdict, dataclass, field
from typing import Any, Optional


@dataclass
class OutcomeIssue:
    code: str  # SYNTAX | MISSING_TARGET | UNEXPECTED_FILE | CREATE_MISSING | DELETE_STILL_PRESENT | MODIFY_MISSING | SYMBOL_MISSING | CONTENT_MISMATCH
    message: str
    files: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "files": list(self.files),
            "evidence": dict(self.evidence),
        }


def verify_python_syntax(
    project_root: str,
    files: list[str],
) -> list[OutcomeIssue]:
    """AST-parse changed Python files. Non-.py files skipped."""
    issues: list[OutcomeIssue] = []
    for rel in files or []:
        if not rel.endswith(".py"):
            continue
        full = os.path.join(project_root, rel)
        if not os.path.isfile(full):
            # existence handled by outcome checks
            continue
        try:
            with open(full, "r", encoding="utf-8", errors="replace") as fh:
                source = fh.read()
            ast.parse(source, filename=rel)
        except SyntaxError as e:
            issues.append(
                OutcomeIssue(
                    code="SYNTAX",
                    message=f"syntax: {rel}: {e.msg} (line {e.lineno})",
                    files=[rel],
                    evidence={
                        "lineno": e.lineno,
                        "offset": e.offset,
                        "text": (e.text or "")[:200],
                        "msg": e.msg,
                    },
                )
            )
        except OSError as e:
            issues.append(
                OutcomeIssue(
                    code="MISSING_TARGET",
                    message=f"syntax: cannot read {rel}: {e}",
                    files=[rel],
                    evidence={},
                )
            )
    return issues


def _plan_step_ops(plan) -> list[dict[str, Any]]:
    """Normalize plan steps into operation descriptors."""
    ops = []
    if plan is None:
        return ops
    for s in getattr(plan, "steps", None) or []:
        ops.append(
            {
                "step_id": getattr(s, "step_id", "") or "",
                "operation_type": getattr(s, "operation_type", "modify") or "modify",
                "target_files": list(getattr(s, "target_files", None) or []),
                "new_text": getattr(s, "new_text", "") or "",
                "content": getattr(s, "content", "") or "",
                "old_text": getattr(s, "old_text", "") or "",
            }
        )
    return ops


def _symbols_defined_in_source(source: str) -> set[str]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
    return names


def verify_plan_outcomes(
    project_root: str,
    plan=None,
    execution_results: Optional[list] = None,
    *,
    expected_symbols: Optional[dict[str, list[str]]] = None,
) -> list[OutcomeIssue]:
    """Check planned mutations against the projected filesystem.

    expected_symbols: optional {file_path: [symbol_name, ...]} that must
    still exist after modify (machine structural check).
    """
    issues: list[OutcomeIssue] = []
    ops = _plan_step_ops(plan)
    planned_targets: set[str] = set()
    for op in ops:
        planned_targets.update(op["target_files"])

    # Actual files reported by execution (successful and not)
    actual_files: set[str] = set()
    for er in execution_results or []:
        for f in list(getattr(er, "files", None) or []):
            actual_files.add(f)
        if isinstance(er, dict):
            for f in er.get("files") or []:
                actual_files.add(f)

    # Per-operation filesystem checks
    for op in ops:
        otype = op["operation_type"]
        for rel in op["target_files"]:
            full = os.path.join(project_root, rel)
            if otype == "create_file":
                if not os.path.isfile(full):
                    issues.append(
                        OutcomeIssue(
                            code="CREATE_MISSING",
                            message=f"outcome: create_file target missing on disk: {rel}",
                            files=[rel],
                            evidence={"step_id": op["step_id"], "operation": otype},
                        )
                    )
                elif op.get("content"):
                    # Weak content check: expected content should appear (or file non-empty)
                    try:
                        with open(full, "r", encoding="utf-8", errors="replace") as fh:
                            body = fh.read()
                    except OSError:
                        body = ""
                    snippet = (op["content"] or "")[:80]
                    if snippet and snippet not in body and body.strip() == "":
                        issues.append(
                            OutcomeIssue(
                                code="CONTENT_MISMATCH",
                                message=f"outcome: create_file produced empty or unexpected content: {rel}",
                                files=[rel],
                                evidence={"step_id": op["step_id"]},
                            )
                        )
            elif otype in ("delete_file", "delete"):
                if os.path.exists(full):
                    issues.append(
                        OutcomeIssue(
                            code="DELETE_STILL_PRESENT",
                            message=f"outcome: delete target still present: {rel}",
                            files=[rel],
                            evidence={"step_id": op["step_id"], "operation": otype},
                        )
                    )
            else:  # modify
                if not os.path.isfile(full):
                    issues.append(
                        OutcomeIssue(
                            code="MODIFY_MISSING",
                            message=f"outcome: modify target missing on disk: {rel}",
                            files=[rel],
                            evidence={"step_id": op["step_id"], "operation": otype},
                        )
                    )
                elif op.get("new_text"):
                    try:
                        with open(full, "r", encoding="utf-8", errors="replace") as fh:
                            body = fh.read()
                    except OSError:
                        body = ""
                    # If new_text is substantial, require a distinctive fragment present
                    nt = op["new_text"]
                    fragment = nt.strip()
                    if len(fragment) > 20:
                        fragment = fragment[:60]
                    if fragment and fragment not in body:
                        # Not hard-fail on every whitespace drift — only when clearly absent
                        # and old_text still fully matches entire file (strong signal of no-op)
                        old = (op.get("old_text") or "").strip()
                        if old and body.strip() == old:
                            issues.append(
                                OutcomeIssue(
                                    code="CONTENT_MISMATCH",
                                    message=f"outcome: modify appears not applied (old content unchanged): {rel}",
                                    files=[rel],
                                    evidence={"step_id": op["step_id"]},
                                )
                            )

    # Unexpected files touched by execution outside plan targets
    if planned_targets and actual_files:
        unexpected = sorted(actual_files - planned_targets)
        if unexpected:
            issues.append(
                OutcomeIssue(
                    code="UNEXPECTED_FILE",
                    message=f"outcome: execution touched files outside plan targets: {unexpected}",
                    files=unexpected,
                    evidence={
                        "planned": sorted(planned_targets),
                        "actual": sorted(actual_files),
                    },
                )
            )

    # Optional expected symbols still present
    for rel, names in (expected_symbols or {}).items():
        full = os.path.join(project_root, rel)
        if not os.path.isfile(full):
            issues.append(
                OutcomeIssue(
                    code="SYMBOL_MISSING",
                    message=f"outcome: expected symbols file missing: {rel}",
                    files=[rel],
                    evidence={"symbols": names},
                )
            )
            continue
        try:
            with open(full, "r", encoding="utf-8", errors="replace") as fh:
                src = fh.read()
        except OSError:
            src = ""
        defined = _symbols_defined_in_source(src)
        for name in names:
            if name not in defined:
                issues.append(
                    OutcomeIssue(
                        code="SYMBOL_MISSING",
                        message=f"outcome: expected symbol '{name}' missing in {rel}",
                        files=[rel],
                        evidence={"symbol": name, "defined": sorted(defined)},
                    )
                )

    return issues


def verify_outcomes(
    project_root: str,
    *,
    plan=None,
    changed_files: Optional[list[str]] = None,
    execution_results: Optional[list] = None,
    expected_symbols: Optional[dict[str, list[str]]] = None,
) -> dict[str, Any]:
    """Run full outcome suite. Returns structured evidence dict.

    {
      "outcome_ok": bool,
      "syntax_ok": bool,
      "issues": [OutcomeIssue.to_dict(), ...],
      "checked_files": [...],
    }
    """
    files = list(changed_files or [])
    if not files and plan is not None:
        for s in getattr(plan, "steps", None) or []:
            files.extend(getattr(s, "target_files", None) or [])
        files = list(dict.fromkeys(files))

    syntax_issues = verify_python_syntax(project_root, files)
    outcome_issues = verify_plan_outcomes(
        project_root,
        plan=plan,
        execution_results=execution_results,
        expected_symbols=expected_symbols,
    )
    all_issues = syntax_issues + outcome_issues
    return {
        "outcome_ok": len(outcome_issues) == 0,
        "syntax_ok": len(syntax_issues) == 0,
        "issues": [i.to_dict() for i in all_issues],
        "checked_files": files,
        "syntax_issue_count": len(syntax_issues),
        "outcome_issue_count": len(outcome_issues),
    }

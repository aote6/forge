"""Constitution adapter — local machine rules (no external rule engine).

Rules enforced here (deterministic, no LLM):
  - forge.runtime_operation_no_content_required: a pure create_object proposal
    has no source content by design (P8) and passes without content.
  - forge.content_required: any mutation proposal (modify / delete / mixed) with
    no content / old_text / new_text fails closed.

Structural checks (operation types, target_files, line ranges, old_text match)
are PlanValidator's responsibility and run before CHECKING.
"""
from __future__ import annotations

from forge.protocols.models import (
    ChangeProposal,
    CheckStatus,
    ConstitutionResult,
    ConstitutionViolation,
)

_RUNTIME_ONLY_RULE = "forge.runtime_operation_no_content_required"
_CONTENT_REQUIRED_RULE = "forge.content_required"


def _op_type(op: dict) -> str:
    return op.get("type") or op.get("operation_type") or ""


def check(proposal: ChangeProposal, project_root: str = ".") -> ConstitutionResult:
    """Check ChangeProposal against local constitution rules."""
    if not isinstance(proposal, ChangeProposal):
        raise TypeError("constitution.check requires ChangeProposal, not bare dict")

    # P8: runtime-only proposals (all create_object) carry no source content.
    if proposal.operations:
        all_runtime = all(
            _op_type(op) == "create_object" for op in proposal.operations
        )
        if all_runtime:
            return ConstitutionResult(
                status=CheckStatus.PASS,
                violations=[],
                checked_rules=[_RUNTIME_ONLY_RULE],
            )

    # Content-less mutation proposals cannot claim content-level PASS.
    has_content = any(
        op.get("content")
        or op.get("old_text")
        or op.get("new_text")
        or op.get("old")
        or op.get("new")
        for op in proposal.operations
    )
    if not has_content:
        return ConstitutionResult(
            status=CheckStatus.FAIL,
            violations=[
                ConstitutionViolation(
                    rule_id=_CONTENT_REQUIRED_RULE,
                    message="ChangeProposal lacks content/old_text/new_text; constitution cannot PASS",
                )
            ],
            checked_rules=[_CONTENT_REQUIRED_RULE],
        )

    # Content present: local rules pass.
    return ConstitutionResult(
        status=CheckStatus.PASS,
        violations=[],
        checked_rules=[_CONTENT_REQUIRED_RULE],
    )

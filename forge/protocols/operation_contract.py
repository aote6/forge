"""Plan-layer operation type contract — single source of truth (SSOT).

Plan / PlanValidator / PlanStep use these strings.
ExecutionAdapter maps Plan types (and legacy aliases) to Intent factories.
IntentType remains the execution-layer SSOT and is intentionally separate
(Plan uses \"modify\"; Intent uses MODIFY_FILE / \"modify_file\").

Legacy aliases exist only for historical ExecutionAdapter inputs.
They are not canonical: Planner must not emit them; PlanValidator must not
accept them; PlanStep must not store them.
"""
from __future__ import annotations

from typing import Any, Iterable, List, Mapping, Optional

# Canonical Plan operation types (Planner output / Validator / PlanStep / checkpoint).
CANONICAL_PLAN_OPERATION_TYPES = frozenset({
    "modify",
    "create_file",
    "delete_file",
    "create_object",
    "link_objects",
})

# ExecutionAdapter-only legacy aliases. Not accepted by PlanValidator.
LEGACY_PLAN_OPERATION_ALIASES = {
    "create": "create_file",
    "delete": "delete_file",
}


class OperationContractError(ValueError):
    """Structure-level operation contract violation (fail-closed)."""


def is_canonical_plan_op(op: str) -> bool:
    return isinstance(op, str) and op in CANONICAL_PLAN_OPERATION_TYPES


def normalize_plan_op_type(raw: str, *, allow_legacy_alias: bool = False) -> str:
    """Return canonical plan op type or raise OperationContractError.

    allow_legacy_alias=True: map create/delete → create_file/delete_file (Adapter).
    allow_legacy_alias=False: only canonical (Validator / PlanStep / checkpoint plan).
    """
    if not isinstance(raw, str) or raw == "":
        raise OperationContractError(
            f"invalid operation type {raw!r}: must be a non-empty string"
        )
    if allow_legacy_alias and raw in LEGACY_PLAN_OPERATION_ALIASES:
        return LEGACY_PLAN_OPERATION_ALIASES[raw]
    if raw not in CANONICAL_PLAN_OPERATION_TYPES:
        raise OperationContractError(
            f"unknown operation type {raw!r}: "
            f"expected one of {sorted(CANONICAL_PLAN_OPERATION_TYPES)}"
            + (
                f" (aliases: {sorted(LEGACY_PLAN_OPERATION_ALIASES)})"
                if allow_legacy_alias
                else ""
            )
        )
    return raw


def require_target_files_list(value: Any, *, field_name: str = "target_files") -> List[Any]:
    """Fail-closed: target_files must be a list (not str, not None when present).

    Does not reinterpret str via list(\"a.py\").
    """
    if not isinstance(value, list):
        raise OperationContractError(
            f"{field_name} must be a list, got {type(value).__name__}"
        )
    return value


def validate_plan_step_structure(step: Any) -> None:
    """Lightweight structure gate for a PlanStep (or duck-typed equivalent)."""
    op = getattr(step, "operation_type", None)
    if op is None or op == "":
        raise OperationContractError(
            "PlanStep missing operation_type "
            "(will not default to modify)"
        )
    if not isinstance(op, str):
        raise OperationContractError(
            f"PlanStep operation_type must be str, got {type(op).__name__}"
        )
    normalize_plan_op_type(op, allow_legacy_alias=False)

    targets = getattr(step, "target_files", None)
    if targets is None:
        raise OperationContractError("PlanStep target_files must be a list, got None")
    require_target_files_list(targets, field_name="PlanStep.target_files")


def validate_proposal_operations_structure(operations: Any) -> None:
    """Lightweight structure gate for ChangeProposal.operations."""
    if not isinstance(operations, list):
        raise OperationContractError(
            f"ChangeProposal.operations must be a list, got {type(operations).__name__}"
        )
    for i, op in enumerate(operations):
        if not isinstance(op, Mapping):
            raise OperationContractError(
                f"operations[{i}] must be a dict, got {type(op).__name__}"
            )
        raw_type = op.get("type")
        raw_op_type = op.get("operation_type")
        has_type = raw_type is not None and raw_type != ""
        has_op_type = raw_op_type is not None and raw_op_type != ""
        if not has_type and not has_op_type:
            raise OperationContractError(
                f"operations[{i}] missing type/operation_type "
                "(will not default to modify)"
            )
        if has_type and has_op_type and raw_type != raw_op_type:
            raise OperationContractError(
                f"operations[{i}] conflicting type and operation_type: "
                f"type={raw_type!r}, operation_type={raw_op_type!r}"
            )
        raw = raw_type if has_type else raw_op_type
        if not isinstance(raw, str):
            raise OperationContractError(
                f"operations[{i}] operation type must be str, got {type(raw).__name__}"
            )
        # Resume may still carry legacy alias ops written by older code paths.
        normalize_plan_op_type(raw, allow_legacy_alias=True)

        if "target_files" in op:
            tf = op.get("target_files")
            if tf is None or not isinstance(tf, list):
                raise OperationContractError(
                    f"operations[{i}].target_files must be a list, "
                    f"got {type(tf).__name__ if tf is not None else 'None'}"
                )


def validate_checkpoint_structure(checkpoint: Any) -> None:
    """Structure-only gate for resumed TaskCheckpoint. No full PlanValidator."""
    plan = getattr(checkpoint, "plan", None)
    if plan is not None:
        for step in getattr(plan, "steps", None) or []:
            validate_plan_step_structure(step)

    proposals = getattr(checkpoint, "change_proposals", None) or []
    if not isinstance(proposals, list):
        raise OperationContractError(
            f"change_proposals must be a list, got {type(proposals).__name__}"
        )
    for p in proposals:
        ops = getattr(p, "operations", None)
        validate_proposal_operations_structure(ops if ops is not None else [])
        tfs = getattr(p, "target_files", None)
        if tfs is not None:
            require_target_files_list(tfs, field_name="ChangeProposal.target_files")

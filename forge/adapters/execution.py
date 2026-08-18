"""Execution adapter — sole mutation boundary for orchestrated tasks.

Path: ChangeProposal -> security resolve -> IntentExecutor -> Veritas commit
      -> Projection (disk materialization). Never writes disk before commit.
Forge MUST NOT forge TransactionDelta fields.
"""
from __future__ import annotations

import json
from typing import List, Optional

from forge.core.security import PathSecurityError, resolve_workspace_path
from forge.intents.executor import IntentExecutionError, IntentExecutor
from forge.intents.intent import Intent
from forge.projections.base import ProjectionManager
from forge.protocols.models import ChangeProposal, ExecutionResult
from forge.world.runtime import WorldRuntime


# Plan operation types come from protocols.operation_contract (SSOT).
# Legacy aliases (create/delete) remain Adapter-only — never fall through to modify.
from forge.protocols.operation_contract import (
    CANONICAL_PLAN_OPERATION_TYPES as _CANONICAL_OP_TYPES,
    LEGACY_PLAN_OPERATION_ALIASES as _OP_TYPE_ALIASES,
)


def _resolve_op_type(op: dict) -> str:
    """Resolve operation type fail-closed: no default, no silent precedence.

    - missing / null / empty on both fields → reject
    - both present and disagree → reject
    - one present → use it
    - both present and equal → use that value
    - unknown after alias map → reject
    """
    raw_type = op.get("type")
    raw_op_type = op.get("operation_type")
    has_type = raw_type is not None and raw_type != ""
    has_op_type = raw_op_type is not None and raw_op_type != ""

    if not has_type and not has_op_type:
        raise IntentExecutionError(
            "missing operation type: provide 'type' or 'operation_type' "
            "(ExecutionAdapter will not default to modify)"
        )
    if has_type and has_op_type and raw_type != raw_op_type:
        raise IntentExecutionError(
            f"conflicting type and operation_type: "
            f"type={raw_type!r}, operation_type={raw_op_type!r}"
        )
    raw = raw_type if has_type else raw_op_type
    if not isinstance(raw, str):
        raise IntentExecutionError(
            f"invalid operation type {raw!r}: must be a non-empty string"
        )
    canonical = _OP_TYPE_ALIASES.get(raw, raw)
    if canonical not in _CANONICAL_OP_TYPES:
        raise IntentExecutionError(
            f"unknown operation type {raw!r}: "
            f"expected one of {sorted(_CANONICAL_OP_TYPES)} "
            f"(aliases: {sorted(_OP_TYPE_ALIASES)})"
        )
    return canonical


class ExecutionAdapter:
    def __init__(
        self,
        world: WorldRuntime,
        projections: ProjectionManager,
        project_root: str,
    ):
        self.world = world
        self.projections = projections
        self.project_root = project_root
        self.executor = IntentExecutor(world)

    def execute_proposal(self, proposal: ChangeProposal) -> ExecutionResult:
        if not isinstance(proposal, ChangeProposal):
            raise TypeError("execute_proposal requires ChangeProposal")

        files: List[str] = []
        intents_list: list = []
        try:
            for op in proposal.operations:
                # P1b: fail-closed Intent boundary — no default, no fallthrough.
                op_type = _resolve_op_type(op)
                # Explicit empty list on the op must not fall back to proposal
                # targets ([] is falsy — never use `op.get(...) or proposal...`).
                # P2: target_files must be a list — never list("a.py") reinterpret.
                if "target_files" in op:
                    raw_targets = op.get("target_files")
                    if raw_targets is None:
                        raise IntentExecutionError(
                            "target_files is null (must be a list; "
                            "ExecutionAdapter will not coerce)"
                        )
                    if not isinstance(raw_targets, list):
                        raise IntentExecutionError(
                            f"target_files must be a list, got "
                            f"{type(raw_targets).__name__} "
                            "(ExecutionAdapter will not reinterpret)"
                        )
                    targets = list(raw_targets)
                else:
                    prop_targets = proposal.target_files
                    if prop_targets is None:
                        raise IntentExecutionError(
                            "proposal.target_files is null (must be a list)"
                        )
                    if not isinstance(prop_targets, list):
                        raise IntentExecutionError(
                            f"proposal.target_files must be a list, got "
                            f"{type(prop_targets).__name__}"
                        )
                    targets = list(prop_targets)

                if op_type == "create_object":
                    # create_object is the sole Intent that allows empty targets.
                    intents_list.append(Intent.create_object(require_confirm=False))
                    continue

                if op_type == "link_objects":
                    # link_objects: World link operation. Requires from_id / to_id / link_type.
                    from_id = op.get("from_id")
                    to_id = op.get("to_id")
                    link_type = op.get("link_type", "owns")
                    if from_id is None or not isinstance(from_id, int):
                        raise IntentExecutionError(
                            "link_objects requires from_id (int)"
                        )
                    if to_id is None or not isinstance(to_id, int):
                        raise IntentExecutionError(
                            "link_objects requires to_id (int)"
                        )
                    intents_list.append(
                        Intent.link_objects(
                            from_id=from_id,
                            to_id=to_id,
                            link_type=link_type,
                            require_confirm=False,
                        )
                    )
                    continue

                if not targets:
                    raise IntentExecutionError(
                        f"target_files required for {op_type}: "
                        "empty targets are not allowed "
                        "(ExecutionAdapter will not silent-skip)"
                    )

                target = targets[0]
                full = resolve_workspace_path(self.project_root, target)
                files.append(target)

                if op_type == "create_file":
                    content = op.get("content", "")
                    intent = Intent.create_file(path=full, content=content, require_confirm=False)
                    intents_list.append(intent)
                elif op_type == "delete_file":
                    object_id = op.get("object_id")
                    if object_id is None:
                        object_id = self._resolve_object_id(full)
                    if object_id is None:
                        raise IntentExecutionError(
                            f"delete requires object_id for path={target}"
                        )
                    intent = Intent.delete_file(path=full, require_confirm=False)
                    intent.parameters["object_id"] = object_id
                    intents_list.append(intent)
                else:
                    # modify (canonical only — unknown already rejected above)
                    object_id = op.get("object_id")
                    if object_id is None:
                        object_id = self._resolve_object_id(full)
                    if object_id is None:
                        raise IntentExecutionError(
                            f"modify requires object_id for path={target}; "
                            "Planner must re-plan or provide object_id"
                        )
                    # P0: sole authoring → machine conversion boundary.
                    from forge.core.edit_contract import (
                        EditContractError,
                        proposal_ops_to_machine,
                    )
                    try:
                        machine_ops = proposal_ops_to_machine(op)
                    except EditContractError as e:
                        raise IntentExecutionError(f"edit_contract: {e}") from e
                    intent = Intent.modify_file(
                        path=full, operations=machine_ops, require_confirm=False
                    )
                    intent.parameters["object_id"] = object_id
                    intents_list.append(intent)

            # Single Veritas transaction for entire proposal.
            if intents_list:
                receipt, delta = self.executor.execute_batch(intents_list)
            else:
                raise IntentExecutionError("proposal has no executable operations")

            # Projection uses real delta from commit — never forged.
            proj_results = self.projections.project(receipt, delta)
            failed = [
                r for r in (proj_results or [])
                if hasattr(r, "success") and not r.success
            ]
            if failed:
                reasons = "; ".join(
                    getattr(r, "reason", "") or r.name for r in failed
                )
                return ExecutionResult(
                    proposal_id=proposal.proposal_id,
                    success=False,
                    tx_id=getattr(receipt, "tx_id", None),
                    world_version=getattr(receipt, "version", None),
                    files=files,
                    error=f"projection_failed: {reasons}",
                    status="WORLD_COMMITTED_PROJECTION_FAILED",
                    receipt_summary={
                        "tx_id": getattr(receipt, "tx_id", None),
                        "version": getattr(receipt, "version", None),
                        "projection_failed": True,
                        "projection_reasons": [
                            getattr(r, "reason", "") for r in failed
                        ],
                        "status": "WORLD_COMMITTED_PROJECTION_FAILED",
                    },
                )

            return ExecutionResult(
                proposal_id=proposal.proposal_id,
                success=True,
                tx_id=getattr(receipt, "tx_id", None),
                world_version=getattr(receipt, "version", None),
                files=files,
                status="COMPLETE",
                receipt_summary={
                    "tx_id": getattr(receipt, "tx_id", None),
                    "version": getattr(receipt, "version", None),
                    "status": "COMPLETE",
                },
            )
        except PathSecurityError as e:
            self._safe_abort()
            return ExecutionResult(
                proposal_id=proposal.proposal_id,
                success=False,
                files=files,
                error=f"path_security: {e}",
                status="ABORTED",
            )
        except IntentExecutionError as e:
            self._safe_abort()
            return ExecutionResult(
                proposal_id=proposal.proposal_id,
                success=False,
                files=files,
                error=str(e),
                status="ABORTED",
            )
        except Exception as e:
            self._safe_abort()
            return ExecutionResult(
                proposal_id=proposal.proposal_id,
                success=False,
                files=files,
                error=f"{type(e).__name__}: {e}",
                status="ABORTED",
            )

    def _resolve_object_id(self, full_path: str) -> Optional[int]:
        """Resolve host path to world object_id via projection path map if available."""
        # Check world._path_map first — even if empty dict, don't fall through to mock.
        world_map = getattr(self.world, "_path_map", None)
        if world_map is not None:
            if hasattr(world_map, "find_object_id"):
                result = world_map.find_object_id(full_path)
                if result is not None:
                    return result
            elif isinstance(world_map, dict):
                if full_path in world_map:
                    return world_map[full_path]
                for oid, p in world_map.items():
                    if p == full_path or str(p) == full_path:
                        try:
                            return int(oid)
                        except (TypeError, ValueError):
                            continue
            # Empty dict or no match — stop here. Do not fall through to mock.
            return None
        proj_map = getattr(self.projections, "object_path_map", None)
        if proj_map is not None:
            if hasattr(proj_map, "find_object_id"):
                result = proj_map.find_object_id(full_path)
                if result is not None:
                    return result
            elif isinstance(proj_map, dict):
                if full_path in proj_map:
                    return proj_map[full_path]
                for oid, p in proj_map.items():
                    if p == full_path or str(p) == full_path:
                        try:
                            return int(oid)
                        except (TypeError, ValueError):
                            continue
        return None

    def _safe_abort(self) -> None:
        try:
            self.world.abort_session()
        except Exception:
            pass

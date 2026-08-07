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
                op_type = op.get("type") or op.get("operation_type") or "modify"
                targets = op.get("target_files") or proposal.target_files
                if not targets:
                    continue
                target = targets[0]
                full = resolve_workspace_path(self.project_root, target)
                files.append(target)

                if op_type in ("create_file", "create"):
                    content = op.get("content", "")
                    intent = Intent.create_file(path=full, content=content, require_confirm=False)
                    intents_list.append(intent)
                elif op_type in ("delete_file", "delete"):
                    object_id = op.get("object_id") or self._resolve_object_id(full)
                    if object_id is None:
                        raise IntentExecutionError(
                            f"delete requires object_id for path={target}"
                        )
                    intent = Intent.delete_file(path=full, require_confirm=False)
                    intent.parameters["object_id"] = object_id
                    intents_list.append(intent)
                else:
                    object_id = op.get("object_id")
                    operations = op.get("operations") or [{
                        "old_text": op.get("old_text", ""),
                        "new_text": op.get("new_text", ""),
                        "start_line": op.get("start_line"),
                        "end_line": op.get("end_line"),
                        "content": op.get("content", ""),
                    }]
                    if object_id is None:
                        object_id = self._resolve_object_id(full)
                    if object_id is None:
                        raise IntentExecutionError(
                            f"modify requires object_id for path={target}; "
                            "Planner must re-plan or provide object_id"
                        )
                    intent = Intent.modify_file(
                        path=full, operations=operations, require_confirm=False
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
                    receipt_summary={
                        "tx_id": getattr(receipt, "tx_id", None),
                        "version": getattr(receipt, "version", None),
                        "projection_failed": True,
                        "projection_reasons": [
                            getattr(r, "reason", "") for r in failed
                        ],
                    },
                )

            return ExecutionResult(
                proposal_id=proposal.proposal_id,
                success=True,
                tx_id=getattr(receipt, "tx_id", None),
                world_version=getattr(receipt, "version", None),
                files=files,
                receipt_summary={
                    "tx_id": getattr(receipt, "tx_id", None),
                    "version": getattr(receipt, "version", None),
                },
            )
        except PathSecurityError as e:
            self._safe_abort()
            return ExecutionResult(
                proposal_id=proposal.proposal_id,
                success=False,
                files=files,
                error=f"path_security: {e}",
            )
        except IntentExecutionError as e:
            self._safe_abort()
            return ExecutionResult(
                proposal_id=proposal.proposal_id,
                success=False,
                files=files,
                error=str(e),
            )
        except Exception as e:
            self._safe_abort()
            return ExecutionResult(
                proposal_id=proposal.proposal_id,
                success=False,
                files=files,
                error=f"{type(e).__name__}: {e}",
            )

    def _resolve_object_id(self, full_path: str) -> Optional[int]:
        """Resolve host path to world object_id via projection path map if available."""
        path_map = getattr(self.world, "_path_map", None) or getattr(
            self.projections, "object_path_map", None
        )
        if not path_map:
            return None
        # Support both ObjectPathMap and plain dict.
        if isinstance(path_map, dict):
            if full_path in path_map:
                return path_map[full_path]
            for oid, p in path_map.items():
                if p == full_path or str(p) == full_path:
                    try:
                        return int(oid)
                    except (TypeError, ValueError):
                        continue
        else:
            # ObjectPathMap or similar: try path→id lookup via known objects.
            # ObjectPathMap is id→path, so we iterate object registry.
            try:
                world_ids = self.world.list_object_ids()
            except Exception:
                world_ids = []
            for oid in world_ids:
                try:
                    mapped = path_map.get(oid)
                    if mapped and (mapped == full_path or str(mapped) == full_path):
                        return int(oid)
                except Exception:
                    continue
        return None

    def _safe_abort(self) -> None:
        try:
            self.world.abort_session()
        except Exception:
            pass

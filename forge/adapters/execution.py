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
        try:
            for op in proposal.operations:
                op_type = op.get("type") or op.get("operation_type") or "modify"
                targets = op.get("target_files") or proposal.target_files
                if not targets:
                    continue
                target = targets[0]
                # Security: every path through resolver
                full = resolve_workspace_path(self.project_root, target)
                files.append(target)

                if op_type in ("create_file", "create"):
                    content = op.get("content", "")
                    intent = Intent.create_file(path=full, content=content, require_confirm=False)
                    receipt, delta = self.executor.execute(intent)
                elif op_type in ("delete_file", "delete"):
                    intent = Intent.delete_file(path=full, require_confirm=False)
                    receipt, delta = self.executor.execute(intent)
                else:
                    # modify: encode operations JSON into intent
                    object_id = op.get("object_id")
                    operations = op.get("operations") or [{
                        "old_text": op.get("old_text", ""),
                        "new_text": op.get("new_text", ""),
                        "start_line": op.get("start_line"),
                        "end_line": op.get("end_line"),
                        "content": op.get("content", ""),
                    }]
                    if object_id is None:
                        # create-or-replace via content if provided
                        content = op.get("new_text") or op.get("content") or ""
                        if content == "" and op.get("old_text"):
                            raise IntentExecutionError(
                                "modify requires object_id or full new content"
                            )
                        intent = Intent.create_file(
                            path=full, content=content, overwrite=True, require_confirm=False
                        )
                        # Intent may not support overwrite flag in older code — handle create path
                        receipt, delta = self.executor.execute(intent)
                    else:
                        intent = Intent.modify_file(
                            path=full, operations=operations, require_confirm=False
                        )
                        intent.parameters["object_id"] = object_id
                        receipt, delta = self.executor.execute(intent)

                # Projection uses real delta from commit — never forged
                self.projections.project(receipt, delta)

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
                error=f"security: {e}",
            )
        except (IntentExecutionError, RuntimeError, OSError, ValueError) as e:
            self._safe_abort()
            return ExecutionResult(
                proposal_id=proposal.proposal_id,
                success=False,
                files=files,
                error=str(e),
            )

    def _safe_abort(self) -> None:
        try:
            self.world.abort_session()
        except Exception:
            pass

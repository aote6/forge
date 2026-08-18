"""EngineeringOrchestrator — unique phase machine for Forge tasks.

Runtime must not re-implement phase logic.
"""
from __future__ import annotations

import sys
import uuid
from typing import Optional

from forge.adapters.constitution import check as constitution_check
from forge.adapters.execution import ExecutionAdapter
from forge.adapters.repo import get_repo_context
from forge.adapters.verification import verify as verification_verify
from forge.memory.checkpoint import CheckpointStore
from forge.orchestrator.phases import MAX_SELF_CORRECTION, TERMINAL, OrchestratorPhase
from forge.planner import Planner
from forge.protocols.models import (
    ChangeProposal,
    CheckStatus,
    Plan,
    PlanStep,
    TaskCheckpoint,
    VerificationRequest,
)
from forge.projections.base import ProjectionManager
from forge.world.runtime import WorldRuntime
from forge.context.index import RepositoryIndex
from forge.failures import (
    FailureClass,
    FailureRecord,
    build_repair_constraints,
    classify_execution_error,
    classify_verification_result,
    is_duplicate_repair,
    repair_attempt_record,
)
from forge.context.snapshot import (
    StaleSnapshotError,
    assert_snapshot_match,
    take_snapshot,
)
from forge.context.planning import (
    collect_plan_target_files,
    content_hashes,
    derive_expected_symbols_for_plan,
)


def plan_to_proposals(plan: Plan) -> list[ChangeProposal]:
    proposals = []
    for step in plan.steps:
        proposals.append(
            ChangeProposal(
                proposal_id=f"{plan.plan_id}_{step.step_id}",
                plan_id=plan.plan_id,
                target_files=list(step.target_files),
                operations=[{
                    "type": step.operation_type,
                    "desc": step.description,
                    "step_id": step.step_id,
                    "target_files": list(step.target_files),
                    "dependencies": list(step.dependencies),
                    "content": step.content,
                    "old_text": step.old_text,
                    "new_text": step.new_text,
                    "start_line": step.start_line,
                    "end_line": step.end_line,
                }],
                reason=f"{plan.goal} — {step.description}",
                expected_effects=[f"{step.operation_type}: {', '.join(step.target_files)}"],
            )
        )
    return proposals


class EngineeringOrchestrator:
    """Single engineering loop. One instance per task run; no shared session across tasks."""

    def __init__(
        self,
        project_root: str,
        world: WorldRuntime,
        projections: ProjectionManager,
        planner: Optional[Planner] = None,
        checkpoint_store: Optional[CheckpointStore] = None,
    ):
        self.project_root = project_root
        self.world = world
        self.projections = projections
        self.planner = planner
        self.store = checkpoint_store or CheckpointStore(project_root)
        self.execution = ExecutionAdapter(world, projections, project_root)

        self.phase = OrchestratorPhase.UNDERSTANDING
        self.checkpoint: Optional[TaskCheckpoint] = None
        self._correction_count = 0
        self._repository_index = None

    def run(self, task: str, task_id: Optional[str] = None) -> str:
        task_id = task_id or f"task_{uuid.uuid4().hex[:12]}"
        try:
            saved = self.store.load(task_id)
        except Exception as e:
            # P2: corrupt checkpoint JSON / contract violation on deserialize.
            from forge.protocols.operation_contract import OperationContractError
            msg = f"checkpoint_load: {type(e).__name__}: {e}"
            print(f"[orchestrator] {msg}", file=sys.stderr)
            self.checkpoint = TaskCheckpoint(
                task_id=task_id,
                phase=OrchestratorPhase.FAILED.value,
                goal=task,
                errors=[msg],
            )
            self.phase = OrchestratorPhase.FAILED
            self.store.save(self.checkpoint)
            return task_id

        if saved and saved.phase not in (
            OrchestratorPhase.COMPLETED.value,
            OrchestratorPhase.FAILED.value,
        ):
            # P2: disk checkpoint is untrusted input — structure gate only
            # (not full PlanValidator). Illegal structure → FAILED, no mutation.
            from forge.protocols.operation_contract import (
                OperationContractError,
                validate_checkpoint_structure,
            )
            try:
                validate_checkpoint_structure(saved)
            except OperationContractError as e:
                saved.errors = list(saved.errors or [])
                saved.errors.append(f"checkpoint_structure: {e}")
                saved.phase = OrchestratorPhase.FAILED.value
                self.checkpoint = saved
                self.phase = OrchestratorPhase.FAILED
                self.store.save(saved)
                print(
                    f"[orchestrator] resume {task_id} REJECT structure: {e}",
                    file=sys.stderr,
                )
                return task_id
            self.checkpoint = saved
            self.phase = OrchestratorPhase(saved.phase)
            # Restore self-correction budget from checkpoint (survive process restart).
            self._correction_count = int(
                (self.checkpoint.extra or {}).get("correction_count") or 0
            )
            # Preserve original goal on resume; do not overwrite with new task string.
            # completed_steps remain authoritative so we continue, not re-execute.
            print(
                f"[orchestrator] resume {task_id} phase={self.phase.value} "
                f"completed={len(self.checkpoint.completed_steps or [])}",
                file=sys.stderr,
            )
        else:
            self.checkpoint = TaskCheckpoint(
                task_id=task_id,
                phase=OrchestratorPhase.UNDERSTANDING.value,
                goal=task,
            )
            self.phase = OrchestratorPhase.UNDERSTANDING
            if task:
                self.checkpoint.goal = task

        while self.phase not in TERMINAL:
            try:
                self._step()
            except Exception as e:
                if self.checkpoint is not None:
                    self.checkpoint.errors.append(str(e))
                    self.phase = OrchestratorPhase.FAILED
                    self.checkpoint.phase = self.phase.value
                    try:
                        self.store.save(self.checkpoint)
                    except Exception:
                        pass
                return f"❌ 任务失败: {e}"

        if self.phase == OrchestratorPhase.COMPLETED:
            plan = self.checkpoint.plan if self.checkpoint else None
            n = len(plan.steps) if plan else 0
            goal = self.checkpoint.goal if self.checkpoint else task
            receipt = self.checkpoint.extra.get("last_receipt") if self.checkpoint else None
            world_version = self.checkpoint.extra.get("last_world_version") if self.checkpoint else None
            detail_lines = []
            if world_version is not None:
                detail_lines.append(f"   world_version: {world_version}")
            if isinstance(receipt, dict):
                for key in ("object_id", "tx_id", "objects_created", "capability_grants"):
                    if key in receipt and receipt[key] not in (None, [], {}):
                        detail_lines.append(f"   {key}: {receipt[key]}")
            detail = ("\n" + "\n".join(detail_lines)) if detail_lines else ""
            return (
                f"✅ 任务完成: {goal}\n"
                f"   步骤: {n} 个\n"
                f"   phase: completed{detail}"
            )
        goal = self.checkpoint.goal if self.checkpoint else task
        errors = list(self.checkpoint.errors) if self.checkpoint else []
        return (
            f"❌ 任务失败: {goal}\n"
            f"   errors: {errors}"
        )

    def _persist(self) -> None:
        assert self.checkpoint is not None
        self.checkpoint.phase = self.phase.value
        self.store.save(self.checkpoint)

    def _step(self) -> None:
        assert self.checkpoint is not None

        if self.phase == OrchestratorPhase.UNDERSTANDING:
            # Priority 1: local machine-verifiable repository snapshot (required).
            snap = take_snapshot(self.project_root)
            self.checkpoint.snapshot_id = snap.snapshot_id
            self.checkpoint.tree_hash = snap.tree_hash
            self.checkpoint.commit_hash = snap.commit_hash
            self.checkpoint.extra["snapshot"] = snap.to_dict()
            # Priority 2: snapshot-bound symbol/reference index (local, no LLM).
            idx = RepositoryIndex.build(self.project_root, snapshot=snap)
            self.checkpoint.extra["repository_index"] = idx.to_summary_dict()
            self._repository_index = idx
            # RepoContext is supplementary understanding from local facts.
            self.checkpoint.repo_context = get_repo_context(self.project_root)
            self.phase = OrchestratorPhase.PLANNING
            self._persist()
            return

        if self.phase == OrchestratorPhase.PLANNING:
            if self.planner is None:
                raise RuntimeError("Planner not configured")
            # Refresh snapshot at plan time (covers VERIFY→PLAN re-entry without UNDERSTAND).
            snap = take_snapshot(self.project_root)
            self.checkpoint.snapshot_id = snap.snapshot_id
            self.checkpoint.tree_hash = snap.tree_hash
            self.checkpoint.commit_hash = snap.commit_hash
            self.checkpoint.extra["snapshot"] = snap.to_dict()
            # Rebuild index on plan snapshot (VERIFY→PLAN or first plan).
            idx = RepositoryIndex.build(self.project_root, snapshot=snap)
            self.checkpoint.extra["repository_index"] = idx.to_summary_dict()
            self._repository_index = idx
            failure = None
            repair_constraints = None
            hist = self.checkpoint.extra.get("failure_history") or []
            if hist:
                candidate = FailureRecord.from_dict(hist[-1])
                # Non-repairable failures (e.g. STALE_SNAPSHOT) must not drive
                # mutation repair and must not re-enter PLAN with the same
                # constraint forever. Fresh understand already ran if we are here
                # after allow_mutation=False → UNDERSTANDING; skip as repair context.
                if not candidate.repairable or candidate.code == FailureClass.STALE_SNAPSHOT.value:
                    failure = None
                    repair_constraints = None
                else:
                    failure = candidate
                    repair_constraints = build_repair_constraints(
                        failure, index=idx
                    )
                    self.checkpoint.extra["repair_constraints"] = repair_constraints.to_dict()
                    if not repair_constraints.allow_mutation:
                        # Defensive: must not produce a mutation plan
                        self.checkpoint.errors.append(
                            f"repair blocked: {failure.code} does not allow mutation; "
                            f"re-understand required"
                        )
                        self.phase = OrchestratorPhase.UNDERSTANDING
                        self._persist()
                        return
            plan, _raw = self.planner.plan(
                self.checkpoint.goal,
                self.checkpoint.repo_context,
                self.project_root,
                index=idx,
                failure=failure,
                repair_constraints=repair_constraints,
            )
            # Duplicate identical repair detection — use repair_attempts, not
            # failure_history (history entries lack plan_signature).
            attempts = list(self.checkpoint.extra.get("repair_attempts") or [])
            if failure is not None and is_duplicate_repair(failure, plan, attempts):
                self.checkpoint.errors.append(
                    f"duplicate repair rejected: failure_signature="
                    f"{failure.signature} plan already attempted"
                )
                self.phase = OrchestratorPhase.FAILED
                self._persist()
                return
            if failure is not None:
                attempts.append(repair_attempt_record(failure, plan))
                self.checkpoint.extra["repair_attempts"] = attempts
            # Normalize to protocol Plan if planner returns legacy type
            if not isinstance(plan, Plan):
                plan = self._coerce_plan(plan)
            # Bind plan to repository snapshot — required engineering invariant.
            plan.snapshot_id = snap.snapshot_id
            plan.tree_hash = snap.tree_hash
            plan.commit_hash = snap.commit_hash
            # Priority 6: machine expected_symbols from same plan-bound index
            derive_expected_symbols_for_plan(plan, idx)
            # Priority 7: persist obligations derived from same index (audit/repair)
            from forge.context.planning import compute_obligations
            self.checkpoint.extra["obligations"] = compute_obligations(
                idx,
                task=self.checkpoint.goal or "",
                repair_constraints=(
                    self.checkpoint.extra.get("repair_constraints")
                ),
            )
            self.checkpoint.plan = plan
            self.phase = OrchestratorPhase.CHECKING
            self._persist()
            return

        if self.phase == OrchestratorPhase.CHECKING:
            plan = self.checkpoint.plan
            if plan is None:
                raise RuntimeError("CHECK without plan — checkpoint corrupt")
            proposals = plan_to_proposals(plan)
            self.checkpoint.change_proposals = proposals
            for p in proposals:
                result = constitution_check(p, self.project_root)
                if result.status == CheckStatus.FAIL:
                    self.checkpoint.errors.append(
                        f"constitution: {[v.rule_id for v in result.violations]}"
                    )
                    self.phase = OrchestratorPhase.FAILED
                    self._persist()
                    return
            self.phase = OrchestratorPhase.EXECUTING
            self._persist()
            return

        if self.phase == OrchestratorPhase.EXECUTING:
            plan = self.checkpoint.plan
            if plan is None:
                raise RuntimeError("EXECUTE without plan")
            # P2 structure gate before any mutation (resume / in-process).
            from forge.protocols.operation_contract import (
                OperationContractError,
                validate_checkpoint_structure,
            )
            try:
                validate_checkpoint_structure(self.checkpoint)
            except OperationContractError as e:
                self.checkpoint.errors.append(f"checkpoint_structure: {e}")
                self.phase = OrchestratorPhase.FAILED
                self._persist()
                return
            # Priority 1: fail-closed if repository changed since plan binding.
            planned_sid = (
                getattr(plan, "snapshot_id", None)
                or self.checkpoint.snapshot_id
                or ""
            )
            try:
                assert_snapshot_match(planned_sid, self.project_root)
            except StaleSnapshotError as e:
                self.checkpoint.errors.append(str(e))
                self.checkpoint.extra["stale_snapshot"] = {
                    "planned_id": e.planned_id,
                    "current_id": e.current_id,
                    "code": e.code,
                }
                frec = FailureRecord(
                    code=FailureClass.STALE_SNAPSHOT.value,
                    message=str(e),
                    phase="executing",
                    files=[],
                    evidence={
                        "planned_id": e.planned_id,
                        "current_id": e.current_id,
                    },
                    retryable=True,
                    repairable=False,
                )
                hist = list(self.checkpoint.extra.get("failure_history") or [])
                hist.append(frec.to_dict())
                self.checkpoint.extra["failure_history"] = hist
                self.checkpoint.extra["last_failure"] = frec.to_dict()
                # Do not call ExecutionAdapter — zero Veritas mutation.
                self.phase = OrchestratorPhase.FAILED
                self._persist()
                return
            # Priority 6: pre-execution content snapshot (once per attempt; resume preserves).
            if not self.checkpoint.extra.get("pre_execution_snapshot"):
                targets = collect_plan_target_files(plan)
                self.checkpoint.extra["pre_execution_snapshot"] = content_hashes(
                    self.project_root, targets
                )
                self._persist()
            proposals = self.checkpoint.change_proposals or plan_to_proposals(plan)
            results = []
            for p in proposals:
                # Skip already completed (including after VERIFY→PLAN re-entry)
                if p.proposal_id in self.checkpoint.completed_steps:
                    continue
                self.checkpoint.current_step = p.proposal_id
                self._persist()
                er = self.execution.execute_proposal(p)
                results.append(er)
                self.checkpoint.execution_results.append(er)
                if not er.success:
                    err = er.error or ""
                    self.checkpoint.errors.append(err)
                    if "projection_failed" in err or (
                        isinstance(er.receipt_summary, dict)
                        and er.receipt_summary.get("projection_failed")
                    ):
                        # World committed; host diverge — do not COMPLETE.
                        self.checkpoint.extra["projection_failed"] = True
                        self.checkpoint.extra["last_receipt"] = er.receipt_summary
                        self.checkpoint.extra["last_tx_id"] = er.tx_id
                        self.checkpoint.extra["last_world_version"] = er.world_version
                    frec = classify_execution_error(
                        err,
                        files=list(getattr(er, "files", None) or []),
                        receipt_summary=getattr(er, "receipt_summary", None) or {},
                        phase="executing",
                    )
                    hist = list(self.checkpoint.extra.get("failure_history") or [])
                    hist.append(frec.to_dict())
                    self.checkpoint.extra["failure_history"] = hist
                    self.checkpoint.extra["last_failure"] = frec.to_dict()
                    # Priority 3: repairable execution failures enter self-correction
                    # (same budget as VERIFY). STALE / non-repairable stay FAILED.
                    if frec.repairable and frec.code != FailureClass.STALE_SNAPSHOT.value:
                        self._correction_count = int(
                            self.checkpoint.extra.get("correction_count") or 0
                        ) + 1
                        self.checkpoint.extra["correction_count"] = self._correction_count
                        if self._correction_count < MAX_SELF_CORRECTION:
                            self.checkpoint.plan = None
                            self.checkpoint.change_proposals = []
                            self.checkpoint.current_step = None
                            self.phase = OrchestratorPhase.PLANNING
                            self._persist()
                            return
                    self.phase = OrchestratorPhase.FAILED
                    self._persist()
                    return
                self.checkpoint.completed_steps.append(p.proposal_id)
                self._persist()
            # Record last receipt and world version for VERIFY and crash recovery.
            if results:
                last_r = results[-1]
                if getattr(last_r, "receipt_summary", None):
                    self.checkpoint.extra["last_receipt"] = last_r.receipt_summary
                if getattr(last_r, "world_version", None) is not None:
                    self.checkpoint.extra["last_committed_version"] = last_r.world_version
            # Record last committed world version for crash recovery.
            # If we crash after this but before VERIFYING persist,
            # we know these transactions are already committed.
            if results:
                last_r = results[-1]
                if getattr(last_r, "world_version", None) is not None:
                    self.checkpoint.extra["last_committed_version"] = last_r.world_version
            self.phase = OrchestratorPhase.VERIFYING
            self._persist()
            return

        if self.phase == OrchestratorPhase.VERIFYING:
            # Crash recovery: if we already committed but crashed before
            # VERIFYING persist, skip re-execution.
            committed_v = self.checkpoint.extra.get("last_committed_version")
            if committed_v is not None and self.world is not None:
                try:
                    current_v = self.world.get_version()
                    if current_v is not None and current_v >= committed_v:
                        # Already committed — proceed to verify.
                        pass
                except Exception:
                    pass
            plan = self.checkpoint.plan
            files = []
            if plan:
                for s in plan.steps:
                    files.extend(s.target_files)
            req = VerificationRequest(changed_files=list(dict.fromkeys(files)))
            # Pass receipt evidence from EXECUTING phase for structured verification.
            last_receipt = self.checkpoint.extra.get("last_receipt")
            last_delta = self.checkpoint.extra.get("last_delta")
            exec_results = self.checkpoint.execution_results
            pre_snap = self.checkpoint.extra.get("pre_execution_snapshot")
            # Priority 8: machine test target selection from index + obligations
            from forge.context.testing import (
                extract_failed_tests_from_history,
                select_verification_targets,
            )
            idx = getattr(self, "_repository_index", None)
            obligations = list(self.checkpoint.extra.get("obligations") or [])
            impact_files = list(getattr(plan, "impact_files", None) or []) if plan else []
            prior_failed = extract_failed_tests_from_history(
                self.checkpoint.extra.get("failure_history") or []
            )
            test_targets = select_verification_targets(
                idx,
                obligations=obligations,
                impact_files=impact_files,
                failed_tests=prior_failed,
                project_root=self.project_root,
            )
            self.checkpoint.extra["test_targets"] = test_targets
            vres = verification_verify(
                req, self.project_root,
                receipt=last_receipt,
                delta=last_delta,
                execution_results=exec_results,
                plan=plan,
                pre_snapshot=pre_snap,
                test_targets=test_targets,
            )
            self.checkpoint.verification_results.append(vres)
            if vres.status == CheckStatus.FAIL:
                # Persist correction budget across resume (in-memory alone is not enough).
                self._correction_count = int(
                    self.checkpoint.extra.get("correction_count") or 0
                ) + 1
                self.checkpoint.extra["correction_count"] = self._correction_count
                # Preserve world evidence — never re-execute committed intents.
                committed = [
                    er.to_dict() if hasattr(er, "to_dict") else er
                    for er in (self.checkpoint.execution_results or [])
                    if getattr(er, "success", False)
                ]
                self.checkpoint.extra["last_verify_failures"] = list(
                    vres.failures or []
                )
                self.checkpoint.extra["committed_receipts"] = committed
                # Priority 3: structured failure classification
                structured = []
                if isinstance(getattr(vres, "evidence", None), dict):
                    structured = list(
                        (vres.evidence or {}).get("structured_failures") or []
                    )
                if not structured:
                    structured = [
                        f.to_dict()
                        for f in classify_verification_result(vres, phase="verifying")
                    ]
                hist = list(self.checkpoint.extra.get("failure_history") or [])
                for s in structured:
                    hist.append(s if isinstance(s, dict) else s)
                self.checkpoint.extra["failure_history"] = hist
                if structured:
                    self.checkpoint.extra["last_failure"] = structured[-1]
                    rc = build_repair_constraints(
                        FailureRecord.from_dict(structured[-1]),
                        index=getattr(self, "_repository_index", None),
                    )
                    self.checkpoint.extra["repair_constraints"] = rc.to_dict()
                self.checkpoint.errors.append(
                    f"verify fail retry {self._correction_count}: {vres.failures}"
                )
                if self._correction_count < MAX_SELF_CORRECTION:
                    # VERIFY FAIL → PLAN only. Keep completed_steps and
                    # execution_results so committed Intents are not replayed.
                    self.checkpoint.plan = None
                    self.checkpoint.change_proposals = []
                    self.checkpoint.current_step = None
                    # completed_steps / execution_results intentionally retained
                    self.phase = OrchestratorPhase.PLANNING
                    self._persist()
                    return
                self.phase = OrchestratorPhase.FAILED
                self.checkpoint.errors.append(f"verify: {vres.failures}")
                self._persist()
                return
            self.phase = OrchestratorPhase.COMPLETED
            self.checkpoint.current_step = None
            self._persist()
            return

        raise RuntimeError(f"unknown phase {self.phase}")

    def _coerce_plan(self, plan) -> Plan:
        from forge.protocols.operation_contract import (
            CANONICAL_PLAN_OPERATION_TYPES,
            OperationContractError,
            require_target_files_list,
        )

        steps = []
        for s in getattr(plan, "steps", []) or []:
            op = getattr(s, "operation_type", None)
            if op is None or op == "":
                raise OperationContractError(
                    "PlanStep missing operation_type "
                    "(_coerce_plan will not default to modify)"
                )
            if not isinstance(op, str):
                raise OperationContractError(
                    f"PlanStep operation_type must be str, got {type(op).__name__}"
                )
            if op not in CANONICAL_PLAN_OPERATION_TYPES:
                raise OperationContractError(
                    f"PlanStep unknown operation_type {op!r}: "
                    f"expected one of {sorted(CANONICAL_PLAN_OPERATION_TYPES)}"
                )
            raw_tf = getattr(s, "target_files", None)
            if raw_tf is None:
                target_files: list = []
            else:
                target_files = list(
                    require_target_files_list(raw_tf, field_name="target_files")
                )
            steps.append(
                PlanStep(
                    step_id=getattr(s, "step_id", ""),
                    description=getattr(s, "description", ""),
                    target_files=target_files,
                    operation_type=op,
                    dependencies=list(getattr(s, "dependencies", []) or []),
                    content=getattr(s, "content", "") or "",
                    old_text=getattr(s, "old_text", "") or "",
                    new_text=getattr(s, "new_text", "") or "",
                    start_line=getattr(s, "start_line", None),
                    end_line=getattr(s, "end_line", None),
                    expected_symbols=list(getattr(s, "expected_symbols", None) or []),
                )
            )
        return Plan(
            plan_id=getattr(plan, "plan_id", ""),
            goal=getattr(plan, "goal", ""),
            steps=steps,
            assumptions=list(getattr(plan, "assumptions", []) or []),
            snapshot_id=getattr(plan, "snapshot_id", "") or "",
            tree_hash=getattr(plan, "tree_hash", "") or "",
            commit_hash=getattr(plan, "commit_hash", "") or "",
            impact_files=list(getattr(plan, "impact_files", []) or []),
            impact_symbols=list(getattr(plan, "impact_symbols", []) or []),
        )

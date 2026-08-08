"""Forge frozen protocol models — cross-system contracts.

Adapters MUST pass these types, never bare dicts for public APIs.
Version field is required for forward compatibility.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional


PROTOCOL_VERSION = "2.0"


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def to_jsonable(obj: Any) -> Any:
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, Enum):
        return obj.value
    if is_dataclass(obj) and not isinstance(obj, type):
        return {k: to_jsonable(v) for k, v in asdict(obj).items()}
    if isinstance(obj, dict):
        return {str(k): to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [to_jsonable(x) for x in obj]
    return str(obj)


class CheckStatus(Enum):
    PASS = "pass"
    FAIL = "fail"
    SKIPPED = "skipped"


class OrchestratorPhase(Enum):
    UNDERSTANDING = "understanding"
    PLANNING = "planning"
    CHECKING = "checking"
    EXECUTING = "executing"
    VERIFYING = "verifying"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class RepoContext:
    version: str = PROTOCOL_VERSION
    repo_id: str = ""
    commit_hash: str = ""
    branch: str = ""
    file_tree: List[str] = field(default_factory=list)
    changed_files: List[str] = field(default_factory=list)
    recent_changes: List[str] = field(default_factory=list)
    status_excerpt: Optional[str] = None

    def to_dict(self) -> dict:
        return to_jsonable(self)

    @classmethod
    def from_dict(cls, data: dict) -> "RepoContext":
        return cls(
            version=data.get("version", PROTOCOL_VERSION),
            repo_id=data.get("repo_id", ""),
            commit_hash=data.get("commit_hash", ""),
            branch=data.get("branch", ""),
            file_tree=list(data.get("file_tree") or []),
            changed_files=list(data.get("changed_files") or []),
            recent_changes=list(data.get("recent_changes") or []),
            status_excerpt=data.get("status_excerpt"),
        )


@dataclass
class PlanStep:
    version: str = PROTOCOL_VERSION
    step_id: str = ""
    description: str = ""
    target_files: List[str] = field(default_factory=list)
    operation_type: str = "modify"
    dependencies: List[str] = field(default_factory=list)
    content: str = ""
    old_text: str = ""
    new_text: str = ""
    start_line: Optional[int] = None
    end_line: Optional[int] = None

    def to_dict(self) -> dict:
        return to_jsonable(self)

    @classmethod
    def from_dict(cls, data: dict) -> "PlanStep":
        return cls(
            version=data.get("version", PROTOCOL_VERSION),
            step_id=data.get("step_id", ""),
            description=data.get("description", ""),
            target_files=list(data.get("target_files") or []),
            operation_type=data.get("operation_type", "modify"),
            dependencies=list(data.get("dependencies") or []),
            content=data.get("content", "") or "",
            old_text=data.get("old_text", "") or "",
            new_text=data.get("new_text", "") or "",
            start_line=data.get("start_line"),
            end_line=data.get("end_line"),
        )


@dataclass
class Plan:
    version: str = PROTOCOL_VERSION
    plan_id: str = ""
    goal: str = ""
    steps: List[PlanStep] = field(default_factory=list)
    assumptions: List[str] = field(default_factory=list)
    # Repository snapshot binding (Priority 1). snapshot_id == tree_hash.
    snapshot_id: str = ""
    tree_hash: str = ""
    commit_hash: str = ""

    def to_dict(self) -> dict:
        return to_jsonable(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Plan":
        steps = [PlanStep.from_dict(s) if isinstance(s, dict) else s for s in (data.get("steps") or [])]
        return cls(
            version=data.get("version", PROTOCOL_VERSION),
            plan_id=data.get("plan_id", ""),
            goal=data.get("goal", ""),
            steps=steps,
            assumptions=list(data.get("assumptions") or []),
            snapshot_id=data.get("snapshot_id", "") or "",
            tree_hash=data.get("tree_hash", "") or "",
            commit_hash=data.get("commit_hash", "") or "",
        )


@dataclass
class ConstitutionViolation:
    rule_id: str
    message: str

    def to_dict(self) -> dict:
        return to_jsonable(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ConstitutionViolation":
        return cls(rule_id=data.get("rule_id", ""), message=data.get("message", ""))


@dataclass
class ChangeProposal:
    version: str = PROTOCOL_VERSION
    proposal_id: str = ""
    plan_id: str = ""
    target_files: List[str] = field(default_factory=list)
    operations: List[Dict[str, Any]] = field(default_factory=list)
    reason: str = ""
    expected_effects: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return to_jsonable(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ChangeProposal":
        return cls(
            version=data.get("version", PROTOCOL_VERSION),
            proposal_id=data.get("proposal_id", ""),
            plan_id=data.get("plan_id", ""),
            target_files=list(data.get("target_files") or []),
            operations=list(data.get("operations") or []),
            reason=data.get("reason", ""),
            expected_effects=list(data.get("expected_effects") or []),
        )


@dataclass
class ConstitutionResult:
    version: str = PROTOCOL_VERSION
    status: CheckStatus = CheckStatus.PASS
    violations: List[ConstitutionViolation] = field(default_factory=list)
    checked_rules: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return to_jsonable(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ConstitutionResult":
        status = data.get("status", "pass")
        if isinstance(status, str):
            status = CheckStatus(status)
        viols = [
            ConstitutionViolation.from_dict(v) if isinstance(v, dict) else v
            for v in (data.get("violations") or [])
        ]
        return cls(
            version=data.get("version", PROTOCOL_VERSION),
            status=status,
            violations=viols,
            checked_rules=list(data.get("checked_rules") or []),
        )


@dataclass
class VerificationRequest:
    version: str = PROTOCOL_VERSION
    changed_files: List[str] = field(default_factory=list)
    change_type: str = "modify"
    hints: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return to_jsonable(self)

    @classmethod
    def from_dict(cls, data: dict) -> "VerificationRequest":
        return cls(
            version=data.get("version", PROTOCOL_VERSION),
            changed_files=list(data.get("changed_files") or []),
            change_type=data.get("change_type", "modify"),
            hints=dict(data.get("hints") or {}),
        )


@dataclass
class VerificationResult:
    """Structured verification result with explicit check categories.

    receipt_ok:   Veritas receipt integrity (tx_id, version, delta)
    projection_ok: File projection matches delta
    build_ok:     SMS/Kuai build/execution result (optional)
    status:       PASS only if all applicable checks pass
    """
    version: str = PROTOCOL_VERSION
    status: CheckStatus = CheckStatus.PASS
    receipt_ok: bool = True
    projection_ok: bool = True
    build_ok: bool | None = None  # None if no build check performed
    executed_checks: List[str] = field(default_factory=list)
    failures: List[str] = field(default_factory=list)
    evidence: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return to_jsonable(self)

    @classmethod
    def from_dict(cls, data: dict) -> "VerificationResult":
        status = data.get("status", "pass")
        if isinstance(status, str):
            status = CheckStatus(status)
        return cls(
            version=data.get("version", PROTOCOL_VERSION),
            status=status,
            receipt_ok=bool(data.get("receipt_ok", True)),
            projection_ok=bool(data.get("projection_ok", True)),
            build_ok=data.get("build_ok"),
            executed_checks=list(data.get("executed_checks") or []),
            failures=list(data.get("failures") or []),
            evidence=dict(data.get("evidence") or {}),
        )


@dataclass
class ExecutionResult:
    version: str = PROTOCOL_VERSION
    proposal_id: str = ""
    success: bool = False
    tx_id: Optional[int] = None
    world_version: Optional[int] = None
    files: List[str] = field(default_factory=list)
    error: str = ""
    receipt_summary: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return to_jsonable(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ExecutionResult":
        if "success" not in data:
            raise ValueError("ExecutionResult.from_dict: missing required field 'success'")
        return cls(
            version=data.get("version", PROTOCOL_VERSION),
            proposal_id=data.get("proposal_id", ""),
            success=bool(data["success"]),
            tx_id=data.get("tx_id"),
            world_version=data.get("world_version"),
            files=list(data.get("files") or []),
            error=data.get("error", "") or "",
            receipt_summary=dict(data.get("receipt_summary") or {}),
        )


@dataclass
class TaskCheckpoint:
    version: str = PROTOCOL_VERSION
    task_id: str = ""
    phase: str = OrchestratorPhase.UNDERSTANDING.value
    plan: Optional[Plan] = None
    completed_steps: List[str] = field(default_factory=list)
    current_step: Optional[str] = None
    repo_context: Optional[RepoContext] = None
    change_proposals: List[ChangeProposal] = field(default_factory=list)
    execution_results: List[ExecutionResult] = field(default_factory=list)
    verification_results: List[VerificationResult] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    goal: str = ""
    created_at: str = field(default_factory=_utcnow)
    updated_at: str = field(default_factory=_utcnow)
    extra: Dict[str, Any] = field(default_factory=dict)
    # Repository snapshot at UNDERSTAND / PLAN time (Priority 1)
    snapshot_id: str = ""
    tree_hash: str = ""
    commit_hash: str = ""

    def to_dict(self) -> dict:
        return to_jsonable(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    @classmethod
    def from_dict(cls, data: dict) -> "TaskCheckpoint":
        plan = data.get("plan")
        if isinstance(plan, dict):
            plan = Plan.from_dict(plan)
        repo = data.get("repo_context")
        if isinstance(repo, dict):
            repo = RepoContext.from_dict(repo)
        proposals = [
            ChangeProposal.from_dict(p) if isinstance(p, dict) else p
            for p in (data.get("change_proposals") or [])
        ]
        exec_results = [
            ExecutionResult.from_dict(e) if isinstance(e, dict) else e
            for e in (data.get("execution_results") or [])
        ]
        ver_results = [
            VerificationResult.from_dict(v) if isinstance(v, dict) else v
            for v in (data.get("verification_results") or [])
        ]
        return cls(
            version=data.get("version", PROTOCOL_VERSION),
            task_id=data.get("task_id", ""),
            phase=data.get("phase", OrchestratorPhase.UNDERSTANDING.value),
            plan=plan,
            completed_steps=list(data.get("completed_steps") or []),
            current_step=data.get("current_step"),
            repo_context=repo,
            change_proposals=proposals,
            execution_results=exec_results,
            verification_results=ver_results,
            errors=list(data.get("errors") or []),
            goal=data.get("goal", "") or "",
            created_at=data.get("created_at") or _utcnow(),
            updated_at=data.get("updated_at") or _utcnow(),
            extra=dict(data.get("extra") or {}),
            snapshot_id=data.get("snapshot_id", "") or "",
            tree_hash=data.get("tree_hash", "") or "",
            commit_hash=data.get("commit_hash", "") or "",
        )

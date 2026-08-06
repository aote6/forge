"""Forge v2 冻结协议 — 所有跨系统数据契约"""
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional
from enum import Enum


class CheckStatus(Enum):
    PASS = "pass"
    FAIL = "fail"


@dataclass
class RepoContext:
    repo_id: str
    commit_hash: str
    branch: str = ""
    file_tree: List[str] = field(default_factory=list)
    recent_commits: List[str] = field(default_factory=list)
    changed_files: List[str] = field(default_factory=list)
    status_excerpt: str = ""


@dataclass
class PlanStep:
    step_id: str
    description: str
    target_files: List[str]
    operation_type: str
    dependencies: List[str] = field(default_factory=list)


@dataclass
class Plan:
    plan_id: str
    goal: str
    steps: List[PlanStep]
    assumptions: List[str] = field(default_factory=list)


@dataclass
class ConstitutionViolation:
    rule_id: str
    message: str


@dataclass
class ChangeProposal:
    proposal_id: str
    plan_id: str
    target_files: List[str]
    operations: List[Dict[str, Any]] = field(default_factory=list)
    reason: str = ""
    expected_effects: List[str] = field(default_factory=list)


@dataclass
class ConstitutionResult:
    status: CheckStatus
    violations: List[ConstitutionViolation] = field(default_factory=list)
    checked_rules: List[str] = field(default_factory=list)


@dataclass
class VerificationRequest:
    changed_files: List[str]
    change_type: str = "modify"
    hints: Dict[str, Any] = field(default_factory=dict)


@dataclass
class VerificationResult:
    status: CheckStatus
    executed_checks: List[str] = field(default_factory=list)
    failures: List[str] = field(default_factory=list)


@dataclass
class TransactionRequest:
    request_id: str
    proposal_id: str
    files: List[Dict[str, str]] = field(default_factory=list)
    reason: str = ""


@dataclass
class TransactionReceipt:
    tx_id: int
    version: int
    success: bool
    error: str = ""


@dataclass
class TaskCheckpoint:
    task_id: str
    phase: str
    plan_id: Optional[str] = None
    completed_steps: List[str] = field(default_factory=list)
    state: Dict[str, Any] = field(default_factory=dict)


@dataclass
class NodeRequest:
    request_id: str
    node: str
    action: str
    payload: Dict[str, Any] = field(default_factory=dict)


@dataclass
class NodeResponse:
    request_id: str
    success: bool
    result: Dict[str, Any] = field(default_factory=dict)
    error: str = ""

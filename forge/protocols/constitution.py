"""宪法检查协议 — 对接 lu"""
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Dict, Any

class CheckStatus(Enum):
    PASS = "pass"
    FAIL = "fail"

@dataclass
class ChangeProposal:
    proposal_id: str
    plan_id: str
    target_files: List[str]
    operations: List[Dict[str, Any]]
    reason: str
    expected_effects: List[str]

@dataclass
class ConstitutionViolation:
    rule_id: str
    message: str

@dataclass
class ConstitutionResult:
    status: CheckStatus
    violations: List[ConstitutionViolation] = field(default_factory=list)
    checked_rules: List[str] = field(default_factory=list)

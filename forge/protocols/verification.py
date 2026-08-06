"""验证协议 — 对接 sms"""
from dataclasses import dataclass, field
from typing import List, Dict, Any
from forge.protocols.constitution import CheckStatus

@dataclass
class VerificationRequest:
    changed_files: List[str]
    change_type: str
    hints: Dict[str, Any] = field(default_factory=dict)

@dataclass
class VerificationResult:
    status: CheckStatus
    executed_checks: List[str]
    failures: List[str] = field(default_factory=list)

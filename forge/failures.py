"""Failure-classified self-correction — machine evidence, not LLM guessing.

Priority 3: structured FailureRecord + deterministic classification +
repair constraints that PlanValidator can enforce.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Optional


class FailureClass(str, Enum):
    MISSING_FILE = "MISSING_FILE"
    STALE_SNAPSHOT = "STALE_SNAPSHOT"
    PROJECTION_FAILURE = "PROJECTION_FAILURE"
    RECEIPT_FAILURE = "RECEIPT_FAILURE"
    BUILD_FAILURE = "BUILD_FAILURE"
    TEST_FAILURE = "TEST_FAILURE"
    SYNTAX_FAILURE = "SYNTAX_FAILURE"
    VALIDATION_FAILURE = "VALIDATION_FAILURE"
    EXECUTION_FAILURE = "EXECUTION_FAILURE"
    UNKNOWN_FAILURE = "UNKNOWN_FAILURE"


@dataclass
class FailureRecord:
    code: str
    message: str
    phase: str = ""
    step_id: str = ""
    files: list[str] = field(default_factory=list)
    evidence: dict[str, Any] = field(default_factory=dict)
    retryable: bool = True
    repairable: bool = True
    signature: str = ""

    def __post_init__(self):
        if not self.signature:
            self.signature = compute_failure_signature(self)

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "phase": self.phase,
            "step_id": self.step_id,
            "files": list(self.files),
            "evidence": dict(self.evidence),
            "retryable": self.retryable,
            "repairable": self.repairable,
            "signature": self.signature,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "FailureRecord":
        return cls(
            code=data.get("code") or FailureClass.UNKNOWN_FAILURE.value,
            message=data.get("message") or "",
            phase=data.get("phase") or "",
            step_id=data.get("step_id") or "",
            files=list(data.get("files") or []),
            evidence=dict(data.get("evidence") or {}),
            retryable=bool(data.get("retryable", True)),
            repairable=bool(data.get("repairable", True)),
            signature=data.get("signature") or "",
        )


def compute_failure_signature(rec: FailureRecord) -> str:
    payload = {
        "code": rec.code,
        "files": sorted(rec.files),
        "step_id": rec.step_id,
        "message_key": (rec.message or "")[:120],
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def compute_plan_signature(plan) -> str:
    """Deterministic signature of plan mutation intent (targets + ops)."""
    steps = []
    for s in getattr(plan, "steps", []) or []:
        steps.append(
            {
                "op": getattr(s, "operation_type", ""),
                "files": sorted(getattr(s, "target_files", []) or []),
                "start": getattr(s, "start_line", None),
                "end": getattr(s, "end_line", None),
            }
        )
    raw = json.dumps(steps, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


@dataclass
class RepairConstraints:
    """Machine-enforced constraints for repair planning (not prompt-only)."""

    failure_code: str
    # modify/delete targets must be subset when non-empty
    required_impact_files: list[str] = field(default_factory=list)
    # at least one step must touch one of these files (when non-empty)
    must_touch_files: list[str] = field(default_factory=list)
    # paths that must use create_file, not modify
    force_create_files: list[str] = field(default_factory=list)
    # operation types forbidden
    forbidden_ops: list[str] = field(default_factory=list)
    # STALE_SNAPSHOT etc.
    allow_mutation: bool = True
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "failure_code": self.failure_code,
            "required_impact_files": list(self.required_impact_files),
            "must_touch_files": list(self.must_touch_files),
            "force_create_files": list(self.force_create_files),
            "forbidden_ops": list(self.forbidden_ops),
            "allow_mutation": self.allow_mutation,
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "RepairConstraints":
        return cls(
            failure_code=data.get("failure_code") or "",
            required_impact_files=list(data.get("required_impact_files") or []),
            must_touch_files=list(data.get("must_touch_files") or []),
            force_create_files=list(data.get("force_create_files") or []),
            forbidden_ops=list(data.get("forbidden_ops") or []),
            allow_mutation=bool(data.get("allow_mutation", True)),
            notes=data.get("notes") or "",
        )


def classify_verification_result(
    vres,
    phase: str = "verifying",
) -> list[FailureRecord]:
    """Deterministic classification from VerificationResult machine fields."""
    records: list[FailureRecord] = []
    failures = list(getattr(vres, "failures", None) or [])
    evidence = dict(getattr(vres, "evidence", None) or {})

    if not getattr(vres, "receipt_ok", True):
        records.append(
            FailureRecord(
                code=FailureClass.RECEIPT_FAILURE.value,
                message="; ".join(f for f in failures if "receipt" in f.lower())
                or "receipt check failed",
                phase=phase,
                files=[],
                evidence={"receipt": evidence.get("receipt"), **{k: evidence[k] for k in evidence if "receipt" in k}},
                retryable=True,
                repairable=True,
            )
        )

    if not getattr(vres, "projection_ok", True):
        missing = []
        for f in failures:
            if "file missing:" in f:
                missing.append(f.split("file missing:", 1)[-1].strip())
        if missing:
            records.append(
                FailureRecord(
                    code=FailureClass.MISSING_FILE.value,
                    message="; ".join(f for f in failures if "missing" in f.lower()),
                    phase=phase,
                    files=missing,
                    evidence={"missing_files": missing},
                    retryable=True,
                    repairable=True,
                )
            )
        else:
            records.append(
                FailureRecord(
                    code=FailureClass.PROJECTION_FAILURE.value,
                    message="; ".join(f for f in failures if "projection" in f.lower())
                    or "projection check failed",
                    phase=phase,
                    files=list(evidence.get("projection_files") or []),
                    evidence=evidence,
                    retryable=True,
                    repairable=True,
                )
            )

    build_ok = getattr(vres, "build_ok", None)
    if build_ok is False:
        # Prefer TEST vs BUILD from evidence, not fuzzy whole-string guess
        build_checks = evidence.get("build_checks") or []
        kind = (evidence.get("build_kind") or evidence.get("failure_kind") or "").lower()
        is_test = kind == "test" or any(
            isinstance(c, str) and "test" in c.lower() for c in build_checks
        )
        # Also accept explicit evidence flag
        if evidence.get("test_failure"):
            is_test = True
        code = FailureClass.TEST_FAILURE.value if is_test else FailureClass.BUILD_FAILURE.value
        records.append(
            FailureRecord(
                code=code,
                message="; ".join(f for f in failures if f.startswith("build:"))
                or f"{code} from SMS/build check",
                phase=phase,
                files=list(evidence.get("failed_files") or []),
                evidence={
                    "build_status": evidence.get("build_status"),
                    "build_checks": build_checks,
                    "test_failure": bool(is_test),
                },
                retryable=True,
                repairable=True,
            )
        )

    # Priority 5: syntax / outcome issues from evidence
    outcome = evidence.get("outcome") or {}
    outcome_issues = list(outcome.get("issues") or [])
    if evidence.get("syntax_ok") is False or any(
        (i.get("code") or "").upper() == "SYNTAX" for i in outcome_issues
    ):
        syn_files = []
        for i in outcome_issues:
            if (i.get("code") or "").upper() == "SYNTAX":
                syn_files.extend(i.get("files") or [])
        # also scrape failures list
        for f in failures:
            if isinstance(f, str) and f.startswith("syntax:"):
                # "syntax: path: msg"
                parts = f.split(":", 2)
                if len(parts) >= 2 and parts[1].strip().endswith(".py"):
                    syn_files.append(parts[1].strip())
        syn_files = sorted(set(syn_files))
        records.append(
            FailureRecord(
                code=FailureClass.SYNTAX_FAILURE.value,
                message="; ".join(f for f in failures if "syntax" in f.lower())
                or "syntax check failed",
                phase=phase,
                files=syn_files,
                evidence={"outcome_issues": [i for i in outcome_issues if (i.get("code") or "").upper() == "SYNTAX"]},
                retryable=True,
                repairable=True,
            )
        )
    elif evidence.get("outcome_ok") is False or any(
        (i.get("code") or "").upper() not in ("", "SYNTAX") for i in outcome_issues
    ):
        out_files = []
        for i in outcome_issues:
            if (i.get("code") or "").upper() != "SYNTAX":
                out_files.extend(i.get("files") or [])
        out_files = sorted(set(out_files))
        # Map outcome codes toward existing failure classes
        codes = {(i.get("code") or "").upper() for i in outcome_issues}
        if codes & {"CREATE_MISSING", "MODIFY_MISSING", "DELETE_STILL_PRESENT"}:
            code = FailureClass.PROJECTION_FAILURE.value
        elif "UNEXPECTED_FILE" in codes:
            code = FailureClass.VALIDATION_FAILURE.value
        else:
            code = FailureClass.PROJECTION_FAILURE.value
        records.append(
            FailureRecord(
                code=code,
                message="; ".join(f for f in failures if "outcome" in f.lower())
                or "outcome verification failed",
                phase=phase,
                files=out_files,
                evidence={"outcome_issues": outcome_issues},
                retryable=True,
                repairable=True,
            )
        )

    if not records and failures:
        records.append(
            FailureRecord(
                code=FailureClass.UNKNOWN_FAILURE.value,
                message="; ".join(failures)[:500],
                phase=phase,
                files=[],
                evidence=evidence,
                retryable=True,
                repairable=True,
            )
        )
    return records


def classify_execution_error(
    error: str,
    files: Optional[list[str]] = None,
    receipt_summary: Optional[dict] = None,
    phase: str = "executing",
) -> FailureRecord:
    err = error or ""
    files = list(files or [])
    receipt_summary = dict(receipt_summary or {})
    if "STALE_SNAPSHOT" in err or "stale_snapshot" in err.lower():
        return FailureRecord(
            code=FailureClass.STALE_SNAPSHOT.value,
            message=err,
            phase=phase,
            files=files,
            evidence=receipt_summary,
            retryable=True,
            repairable=False,  # must re-understand, not mutate on stale plan
        )
    if "projection_failed" in err or receipt_summary.get("projection_failed"):
        return FailureRecord(
            code=FailureClass.PROJECTION_FAILURE.value,
            message=err,
            phase=phase,
            files=files,
            evidence=receipt_summary,
            retryable=True,
            repairable=True,
        )
    if "path_security" in err or "PlanValidation" in err or "validation" in err.lower():
        return FailureRecord(
            code=FailureClass.VALIDATION_FAILURE.value,
            message=err,
            phase=phase,
            files=files,
            evidence={},
            retryable=True,
            repairable=True,
        )
    if "SyntaxError" in err or "syntax" in err.lower():
        return FailureRecord(
            code=FailureClass.SYNTAX_FAILURE.value,
            message=err,
            phase=phase,
            files=files,
            evidence={},
            retryable=True,
            repairable=True,
        )
    if err:
        return FailureRecord(
            code=FailureClass.EXECUTION_FAILURE.value,
            message=err,
            phase=phase,
            files=files,
            evidence=receipt_summary,
            retryable=True,
            repairable=True,
        )
    return FailureRecord(
        code=FailureClass.UNKNOWN_FAILURE.value,
        message="unknown execution failure",
        phase=phase,
        files=files,
        evidence=receipt_summary,
    )


def build_repair_constraints(
    failure: FailureRecord,
    index=None,
) -> RepairConstraints:
    """Map FailureRecord → machine constraints for Planner/Validator."""
    code = failure.code
    files = list(failure.files)

    if code == FailureClass.STALE_SNAPSHOT.value:
        return RepairConstraints(
            failure_code=code,
            allow_mutation=False,
            notes="Must refresh snapshot and re-plan; no mutation on stale plan",
        )

    if code == FailureClass.MISSING_FILE.value:
        return RepairConstraints(
            failure_code=code,
            force_create_files=files,
            must_touch_files=files,
            forbidden_ops=[],  # modify of missing rejected by existing validator
            notes="Missing files require create_file, not modify",
        )

    if code == FailureClass.SYNTAX_FAILURE.value:
        return RepairConstraints(
            failure_code=code,
            required_impact_files=files,
            must_touch_files=files,
            notes="Repair must touch syntax-failed files",
        )

    if code in (FailureClass.TEST_FAILURE.value, FailureClass.BUILD_FAILURE.value):
        impact = list(files)
        if index is not None and files:
            for f in files:
                # file-level only unless we can map to symbols safely — skip symbol guess
                pass
            # expand via index: any symbol defined in failing file
            try:
                for sym in getattr(index, "symbols", []) or []:
                    if sym.file_path in files:
                        impact.extend(index.affected_files(sym.name))
            except Exception:
                pass
        impact = sorted(set(impact))
        return RepairConstraints(
            failure_code=code,
            required_impact_files=impact if impact else files,
            must_touch_files=files if files else impact,
            notes=f"{code}: repair limited to impact of failing files",
        )

    if code == FailureClass.PROJECTION_FAILURE.value:
        return RepairConstraints(
            failure_code=code,
            # Do not force source edits; empty must_touch — validator only blocks empty plans
            required_impact_files=[],
            must_touch_files=[],
            forbidden_ops=[],
            notes="Projection failure: prefer recovery evidence; avoid blind re-mutation",
        )

    if code == FailureClass.VALIDATION_FAILURE.value:
        return RepairConstraints(
            failure_code=code,
            notes="Must satisfy validator constraints that previously failed",
        )

    return RepairConstraints(
        failure_code=code or FailureClass.UNKNOWN_FAILURE.value,
        notes="Unknown failure — minimal constraints",
    )


def is_duplicate_repair(
    failure: FailureRecord,
    plan,
    history: list[dict],
) -> bool:
    """True if same failure signature + same plan signature already attempted."""
    plan_sig = compute_plan_signature(plan)
    fail_sig = failure.signature or compute_failure_signature(failure)
    for entry in history or []:
        if entry.get("failure_signature") == fail_sig and entry.get("plan_signature") == plan_sig:
            return True
    return False


def repair_attempt_record(failure: FailureRecord, plan) -> dict:
    return {
        "failure_signature": failure.signature or compute_failure_signature(failure),
        "failure_code": failure.code,
        "plan_signature": compute_plan_signature(plan),
        "plan_id": getattr(plan, "plan_id", ""),
        "targets": [
            (getattr(s, "operation_type", ""), list(getattr(s, "target_files", []) or []))
            for s in (getattr(plan, "steps", None) or [])
        ],
    }

"""Verification adapter — receipt + projection + outcome + optional build check.

VERIFY semantics (v2.2 / Priority 5):
  1. Receipt integrity: tx_id, version, delta present and valid
  2. Projection consistency: planned files exist / deleted as expected
  3. Outcome reliability: plan ops vs filesystem, Python syntax on changed files
  4. Build check (optional): SMS/Kuai execution result

PASS requires all applicable checks to pass.
Kuai's "no exception" is NOT treated as verification success;
it is only one component of the build_check.
LLM claims are never treated as verification facts.
"""
from __future__ import annotations

import os
from typing import Any

from forge.adapters.hub_client import HubClient
from forge.protocols.models import CheckStatus, VerificationRequest, VerificationResult


def verify(
    request: VerificationRequest,
    project_root: str = ".",
    hub: HubClient | None = None,
    *,
    receipt: Any = None,
    delta: Any = None,
    execution_results: list | None = None,
    plan: Any = None,
    expected_symbols: dict | None = None,
    skip_build: bool = False,
) -> VerificationResult:
    """Run structured verification: receipt → projection → outcome → build.

    Args:
        request: VerificationRequest with changed_files
        project_root: project directory
        hub: HubClient for SMS/Kuai (optional)
        receipt: Veritas receipt from EXECUTING phase
        delta: TransactionDelta from EXECUTING phase
        execution_results: list of ExecutionResult from EXECUTING phase
        plan: optional Plan for outcome alignment checks (P5)
        expected_symbols: optional {file: [symbol, ...]} structural expectations
        skip_build: if True, do not invoke SMS (useful for unit tests)
    """
    if not isinstance(request, VerificationRequest):
        raise TypeError("verification.verify requires VerificationRequest")

    checks: list[str] = []
    failures: list[str] = []
    evidence: dict[str, Any] = {}
    receipt_ok = True
    projection_ok = True
    outcome_ok = True
    syntax_ok = True
    build_ok: bool | None = None

    # ── 1. Receipt verification ──────────────────────────────
    if receipt is not None:
        checks.append("receipt")
        # receipt may be a dict (from checkpoint) or a Receipt object.
        if isinstance(receipt, dict):
            tx_id = receipt.get("tx_id")
            version = receipt.get("version")
        else:
            tx_id = getattr(receipt, "tx_id", None)
            version = getattr(receipt, "version", None)
        if tx_id is None or version is None:
            receipt_ok = False
            failures.append("receipt: missing tx_id or version")
        else:
            evidence["receipt_tx_id"] = tx_id
            evidence["receipt_version"] = version
            if delta is not None:
                if isinstance(delta, dict):
                    evidence["delta_objects_created"] = delta.get("objects_created", [])
                    evidence["delta_memory_written"] = len(delta.get("memory_written", []))
                else:
                    evidence["delta_objects_created"] = getattr(delta, "objects_created", [])
                    evidence["delta_memory_written"] = len(getattr(delta, "memory_written", []))
    else:
        # No receipt provided — fail closed.
        # Receipt is required for world state verification.
        checks.append("receipt")
        receipt_ok = False
        failures.append("receipt: not provided — cannot verify world state")
        evidence["receipt"] = "missing"

    # ── 2. Projection consistency (existence of non-delete targets) ──
    delete_targets: set[str] = set()
    if plan is not None:
        for s in getattr(plan, "steps", None) or []:
            op = getattr(s, "operation_type", "") or ""
            if op in ("delete_file", "delete"):
                delete_targets.update(getattr(s, "target_files", None) or [])

    if request.changed_files:
        checks.append("projection")
        for f in request.changed_files:
            if f in delete_targets:
                continue  # delete targets must NOT exist — checked in outcome
            full = os.path.join(project_root, f)
            if not os.path.exists(full):
                projection_ok = False
                failures.append(f"projection: file missing: {f}")
        if projection_ok:
            evidence["projection_files_checked"] = len(request.changed_files)

    # ── 3. Outcome reliability (Priority 5) ───────────────────
    try:
        from forge.verification.outcome import verify_outcomes

        outcome = verify_outcomes(
            project_root,
            plan=plan,
            changed_files=list(request.changed_files or []),
            execution_results=execution_results,
            expected_symbols=expected_symbols,
        )
        checks.append("outcome")
        evidence["outcome"] = {
            "outcome_ok": outcome.get("outcome_ok"),
            "syntax_ok": outcome.get("syntax_ok"),
            "checked_files": outcome.get("checked_files"),
            "issue_count": len(outcome.get("issues") or []),
            "issues": outcome.get("issues") or [],
        }
        outcome_ok = bool(outcome.get("outcome_ok", True))
        syntax_ok = bool(outcome.get("syntax_ok", True))
        for issue in outcome.get("issues") or []:
            code = (issue.get("code") or "").upper()
            msg = issue.get("message") or code
            if code == "SYNTAX":
                failures.append(msg if msg.startswith("syntax:") else f"syntax: {msg}")
            else:
                failures.append(msg if msg.startswith("outcome:") else f"outcome: {msg}")
    except Exception as e:
        checks.append("outcome")
        outcome_ok = False
        failures.append(f"outcome: verification error: {e}")
        evidence["outcome_error"] = str(e)

    evidence["outcome_ok"] = outcome_ok
    evidence["syntax_ok"] = syntax_ok

    # ── 4. Build check (SMS/Kuai, optional) ──────────────────
    if not skip_build:
        client = hub or HubClient(project_root=project_root)
        try:
            resp = client.invoke(
                capability="sms",
                action="verify",
                payload=request.to_dict(),
                timeout=120,
            )
            if resp.ok:
                data = resp.data if isinstance(resp.data, dict) else {}
                build_status = data.get("status", "pass")
                build_ok = build_status == "pass"
                checks.append("build")
                # Normalize build/test evidence for P3 classifier
                evidence["build_status"] = build_status
                evidence["build_checks"] = data.get("executed_checks", [])
                evidence["build_evidence"] = {
                    "command": data.get("command") or data.get("cmd"),
                    "exit_code": data.get("exit_code"),
                    "stdout_excerpt": (data.get("stdout") or data.get("stdout_excerpt") or "")[:500],
                    "stderr_excerpt": (data.get("stderr") or data.get("stderr_excerpt") or "")[:500],
                    "duration": data.get("duration"),
                    "failed_tests": data.get("failed_tests") or data.get("failures") or [],
                    "failed_files": data.get("failed_files") or [],
                }
                if data.get("test_failure") or data.get("failure_kind") == "test":
                    evidence["test_failure"] = True
                    evidence["failure_kind"] = "test"
                if data.get("failed_files"):
                    evidence["failed_files"] = list(data.get("failed_files") or [])
                if not build_ok:
                    failures.append(f"build: {data.get('failures', ['build failed'])}")
            else:
                checks.append("build")
                build_ok = False
                failures.append(f"build: sms unavailable: {resp.error}")
                evidence["build_evidence"] = {
                    "exit_code": None,
                    "stderr_excerpt": str(resp.error or "")[:500],
                }
        except Exception as e:
            checks.append("build")
            build_ok = False
            failures.append(f"build: {e}")
            evidence["build_evidence"] = {"stderr_excerpt": str(e)[:500]}

    # ── 5. Aggregate status ──────────────────────────────────
    all_ok = receipt_ok and projection_ok and outcome_ok and syntax_ok
    if build_ok is not None:
        all_ok = all_ok and build_ok

    result = VerificationResult(
        status=CheckStatus.PASS if all_ok else CheckStatus.FAIL,
        receipt_ok=receipt_ok,
        projection_ok=projection_ok,
        build_ok=build_ok,
        executed_checks=checks,
        failures=failures,
        evidence=evidence,
    )
    if result.status != CheckStatus.PASS:
        from forge.failures import classify_verification_result
        structured = classify_verification_result(result, phase="verifying")
        result.evidence = dict(result.evidence or {})
        result.evidence["structured_failures"] = [f.to_dict() for f in structured]
    return result

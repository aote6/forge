"""Verification adapter — receipt + projection + optional build check.

VERIFY semantics (v2.1):
  1. Receipt integrity: tx_id, version, delta present and valid
  2. Projection consistency: files on disk match delta
  3. Build check (optional): SMS/Kuai execution result

PASS requires all applicable checks to pass.
Kuai's "no exception" is NOT treated as verification success;
it is only one component of the build_check.
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
) -> VerificationResult:
    """Run structured verification: receipt → projection → build.

    Args:
        request: VerificationRequest with changed_files
        project_root: project directory
        hub: HubClient for SMS/Kuai (optional)
        receipt: Veritas receipt from EXECUTING phase
        delta: TransactionDelta from EXECUTING phase
        execution_results: list of ExecutionResult from EXECUTING phase
    """
    if not isinstance(request, VerificationRequest):
        raise TypeError("verification.verify requires VerificationRequest")

    checks: list[str] = []
    failures: list[str] = []
    evidence: dict[str, Any] = {}
    receipt_ok = True
    projection_ok = True
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

    # ── 2. Projection consistency ────────────────────────────
    if request.changed_files:
        checks.append("projection")
        for f in request.changed_files:
            full = os.path.join(project_root, f)
            if not os.path.exists(full):
                projection_ok = False
                failures.append(f"projection: file missing: {f}")
        if projection_ok:
            evidence["projection_files_checked"] = len(request.changed_files)

    # ── 3. Build check (SMS/Kuai, optional) ──────────────────
    client = hub or HubClient(project_root=project_root)
    try:
        resp = client.invoke(
            capability="sms",
            action="verify",
            payload=request.to_dict(),
            timeout=120,
        )
        if resp.ok:
            data = resp.data
            build_status = data.get("status", "pass")
            build_ok = build_status == "pass"
            checks.append("build")
            evidence["build_status"] = build_status
            evidence["build_checks"] = data.get("executed_checks", [])
            if not build_ok:
                failures.append(f"build: {data.get('failures', ['build failed'])}")
        else:
            checks.append("build")
            build_ok = False
            failures.append(f"build: sms unavailable: {resp.error}")
    except Exception as e:
        checks.append("build")
        build_ok = False
        failures.append(f"build: {e}")

    # ── 4. Aggregate status ──────────────────────────────────
    all_ok = receipt_ok and projection_ok
    if build_ok is not None:
        all_ok = all_ok and build_ok

    return VerificationResult(
        status=CheckStatus.PASS if all_ok else CheckStatus.FAIL,
        receipt_ok=receipt_ok,
        projection_ok=projection_ok,
        build_ok=build_ok,
        executed_checks=checks,
        failures=failures,
        evidence=evidence,
    )

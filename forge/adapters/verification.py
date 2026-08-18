"""Verification adapter — receipt + projection + outcome + local test build.

VERIFY semantics (v2.2 / Priority 5):
  1. Receipt integrity: tx_id, version, delta present and valid
  2. Projection consistency: planned files exist / deleted as expected
  3. Outcome reliability: plan ops vs filesystem, Python syntax on changed files
  4. Test build (optional): local pytest on selected test files

PASS requires all applicable checks to pass.
The local test runner's "no exception" is NOT treated as verification success;
it is only one component of the build check.
LLM claims are never treated as verification facts.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from typing import Any

from forge.protocols.models import CheckStatus, VerificationRequest, VerificationResult


def _looks_like_test(path: str) -> bool:
    p = (path or "").replace("\\", "/").lower()
    base = p.rsplit("/", 1)[-1]
    return (
        "/tests/" in f"/{p}"
        or p.startswith("tests/")
        or base.startswith("test_")
        or base.endswith("_test.py")
    )


def _parse_failed_tests(output: str) -> list[str]:
    """Extract failed node ids from pytest summary (FAILED <nodeid> ...)."""
    failed: list[str] = []
    for line in output.splitlines():
        line = line.strip()
        if line.startswith("FAILED "):
            node = line[len("FAILED "):].split(" ", 1)[0].strip()
            if node and "::" in node:
                failed.append(node)
    return sorted(set(failed))


def _summary_line(output: str) -> str:
    for line in reversed(output.splitlines()):
        line = line.strip()
        if line and ("failed" in line or "error" in line.lower()):
            return line[:200]
    return ""


def _run_local_tests(
    request: VerificationRequest,
    project_root: str,
    test_targets: dict | None,
) -> tuple[bool, dict[str, Any], list[str]]:
    """Run local pytest on selected test files. Returns (build_ok, evidence, failures).

    No external test scheduler. Deterministic subprocess pytest.
    """
    test_files: list[str] = []
    if test_targets:
        test_files = list(test_targets.get("test_files") or [])
    if not test_files:
        hints = getattr(request, "hints", None) or {}
        test_files = list(hints.get("test_files") or [])
    if not test_files:
        test_files = [
            f for f in (request.changed_files or []) if _looks_like_test(f)
        ]
    test_files = sorted(set(test_files))

    def _empty_evidence() -> dict[str, Any]:
        return {
            "build_status": "pass",
            "build_checks": [],
            "build_evidence": {
                "command": None,
                "exit_code": 0,
                "stdout_excerpt": "",
                "stderr_excerpt": "",
                "duration": 0.0,
                "failed_tests": [],
                "failed_files": [],
            },
        }

    if not test_files:
        # No tests selected → nothing to run; not a failure.
        return True, _empty_evidence(), []

    cmd = [sys.executable, "-m", "pytest", "-q", *test_files]
    started = time.monotonic()
    try:
        proc = subprocess.run(
            cmd,
            cwd=project_root,
            capture_output=True,
            text=True,
            timeout=120,
        )
    except FileNotFoundError:
        ev = _empty_evidence()
        ev["build_status"] = "fail"
        ev["build_checks"] = ["pytest"]
        ev["build_evidence"]["command"] = " ".join(cmd)
        ev["build_evidence"]["stderr_excerpt"] = "pytest interpreter not found"
        return False, ev, ["build: pytest not found"]
    except subprocess.TimeoutExpired:
        ev = _empty_evidence()
        ev["build_status"] = "fail"
        ev["build_checks"] = ["pytest"]
        ev["build_evidence"]["command"] = " ".join(cmd)
        ev["build_evidence"]["stderr_excerpt"] = "pytest timeout after 120s"
        return False, ev, ["build: pytest timeout after 120s"]

    duration = time.monotonic() - started
    stdout = proc.stdout or ""
    stderr = proc.stderr or ""
    combined = stdout + "\n" + stderr

    failed_tests = _parse_failed_tests(combined)
    failed_files = sorted({t.split("::", 1)[0] for t in failed_tests})

    build_ok = proc.returncode == 0
    evidence: dict[str, Any] = {
        "build_status": "pass" if build_ok else "fail",
        "build_checks": ["pytest"],
        "build_evidence": {
            "command": " ".join(cmd),
            "exit_code": proc.returncode,
            "stdout_excerpt": stdout[:2000],
            "stderr_excerpt": stderr[:2000],
            "duration": round(duration, 3),
            "failed_tests": failed_tests,
            "failed_files": failed_files,
        },
    }
    if not build_ok:
        evidence["test_failure"] = True
        evidence["failure_kind"] = "test"
        if failed_files:
            evidence["failed_files"] = failed_files
        if failed_tests:
            evidence["test_results"] = [
                {"test_name": t, "status": "failed", "file": t.split("::", 1)[0]}
                for t in failed_tests
            ]
        summary = _summary_line(combined)
        failures = [
            f"build: pytest failed ({proc.returncode})"
            + (f": {summary}" if summary else "")
        ]
    else:
        failures = []
    return build_ok, evidence, failures


def verify(
    request: VerificationRequest,
    project_root: str = ".",
    *,
    receipt: Any = None,
    delta: Any = None,
    execution_results: list | None = None,
    plan: Any = None,
    expected_symbols: dict | None = None,
    pre_snapshot: dict | None = None,
    test_targets: dict | None = None,
    skip_build: bool = False,
) -> VerificationResult:
    """Run structured verification: receipt → projection → outcome → build.

    Args:
        request: VerificationRequest with changed_files
        project_root: project directory
        receipt: Veritas receipt from EXECUTING phase
        delta: TransactionDelta from EXECUTING phase
        execution_results: list of ExecutionResult from EXECUTING phase
        plan: optional Plan for outcome alignment checks (P5)
        expected_symbols: optional {file: [symbol, ...]} structural expectations
        skip_build: if True, do not run local pytest (useful for unit tests)
        pre_snapshot: optional {path: sha256} from EXECUTE entry (P6)
        test_targets: optional P8 selection dict (test_files / required / ...)
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
        from forge.context.planning import plan_expected_symbols_map

        # P6: auto-consume PlanStep.expected_symbols when caller omits map
        sym_map = expected_symbols
        if sym_map is None and plan is not None:
            sym_map = plan_expected_symbols_map(plan)

        outcome = verify_outcomes(
            project_root,
            plan=plan,
            changed_files=list(request.changed_files or []),
            execution_results=execution_results,
            expected_symbols=sym_map,
            pre_snapshot=pre_snapshot,
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

    # ── 4. Test build (local pytest, optional) ────────────────
    # P8: attach machine test_targets for selection / evidence.
    if test_targets:
        evidence["test_selection"] = {
            "test_files": list(test_targets.get("test_files") or []),
            "required": list(test_targets.get("required") or []),
            "advisory": list(test_targets.get("advisory") or []),
            "forced_failed": list(test_targets.get("forced_failed") or []),
            "empty": bool(test_targets.get("empty")),
            "reasons": dict(test_targets.get("reasons") or {}),
        }
        hints = dict(getattr(request, "hints", None) or {})
        hints["test_targets"] = evidence["test_selection"]
        hints["test_files"] = list(test_targets.get("test_files") or [])
        if test_targets.get("forced_failed"):
            hints["failed_tests"] = list(test_targets.get("forced_failed") or [])
        request.hints = hints

    if not skip_build:
        checks.append("build")
        try:
            build_ok, build_evidence, build_failures = _run_local_tests(
                request, project_root, test_targets
            )
            evidence.update(build_evidence)
            failures.extend(build_failures)
            if not build_ok:
                # Selected targets failed → mark for classifier.
                if test_targets and not test_targets.get("empty"):
                    evidence["selected_test_failure"] = True
        except Exception as e:
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

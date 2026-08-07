"""Lu decision adapter — Constitution only.

Lu MUST NOT write the workspace. All host file mutation goes through:

    Intent → Veritas → Projection

This module only returns decision / evidence / rule_ids. Write helpers
are intentionally disabled (raise RuntimeError).
"""
from __future__ import annotations

import os
import subprocess
import sys
from typing import Any, Dict, List, Optional, Tuple

LU_PATCH = os.path.expanduser("~/lu/core/lu_patch.py")


class LuWriteForbidden(RuntimeError):
    """Raised when a caller attempts to use Lu as a write path."""


def _decision(
    decision: str,
    evidence: str = "",
    rule_ids: Optional[List[str]] = None,
    **extra: Any,
) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "decision": decision,  # "ALLOW" | "DENY"
        "evidence": evidence,
        "rule_ids": list(rule_ids or []),
    }
    out.update(extra)
    return out


def check(
    target: str,
    old_text: str = "",
    new_text: str = "",
    timeout: int = 30,
) -> Dict[str, Any]:
    """Ask Lu for a constitution decision. Never mutates the workspace.

    Returns: {decision, evidence, rule_ids}
    """
    if not os.path.exists(LU_PATCH):
        return _decision(
            "DENY",
            evidence=f"Lu patch engine missing: {LU_PATCH}",
            rule_ids=["lu.unavailable"],
        )

    # Prefer --check mode if available; fall back to dry decision via stdin protocol.
    cmd = ["python3", LU_PATCH, target, "--check"]
    if old_text:
        cmd += ["--old-text", old_text]
    if new_text:
        cmd += ["--text", new_text]

    print(f"  [Lu:check] {target}", file=sys.stderr)
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        return _decision("DENY", evidence="lu check timeout", rule_ids=["lu.timeout"])
    except OSError as e:
        return _decision("DENY", evidence=str(e), rule_ids=["lu.os_error"])

    stdout = (result.stdout or "").strip()
    stderr = (result.stderr or "").strip()

    if result.returncode == 0:
        return _decision(
            "ALLOW",
            evidence=stdout or "lu check passed",
            rule_ids=["lu"],
        )
    return _decision(
        "DENY",
        evidence=stderr or stdout or "lu check failed",
        rule_ids=["lu"],
    )


# ── Write API: permanently disabled ──────────────────────────────────────────


def patch(*_args, **_kwargs) -> Tuple[bool, str]:
    raise LuWriteForbidden(
        "Lu write path disabled. Use Intent → Veritas → Projection."
    )


def create(*_args, **_kwargs) -> Tuple[bool, str]:
    raise LuWriteForbidden(
        "Lu write path disabled. Use Intent → Veritas → Projection."
    )


def delete(*_args, **_kwargs) -> Tuple[bool, str]:
    raise LuWriteForbidden(
        "Lu write path disabled. Use Intent → Veritas → Projection."
    )


def snapshot(*_args, **_kwargs) -> Tuple[bool, str]:
    raise LuWriteForbidden(
        "Lu write path disabled. Use Intent → Veritas → Projection."
    )


def rollback(*_args, **_kwargs) -> Tuple[bool, str]:
    raise LuWriteForbidden(
        "Lu write path disabled. Use Intent → Veritas → Projection."
    )

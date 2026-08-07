"""DEPRECATED direct-node Hub adapter.

Production code MUST use forge.adapters.hub_client.HubClient.invoke().

This module no longer spawns ~/hub/nodes/*/main.py. All public functions
delegate to HubClient or raise, so there is no Forge → node script bypass.
"""
from __future__ import annotations

import warnings
from typing import List, Optional

from forge.adapters.hub_client import HubClient
from forge.protocols.models import (
    CheckStatus,
    ConstitutionResult,
    ConstitutionViolation,
    RepoContext,
    VerificationResult,
)


def _warn_deprecated(name: str) -> None:
    warnings.warn(
        f"hub_adapter.{name} is deprecated; use HubClient.invoke() via "
        f"repo / constitution / verification adapters.",
        DeprecationWarning,
        stacklevel=3,
    )


def _call_node(*_args, **_kwargs) -> dict:
    """Removed. Direct node subprocess is forbidden."""
    raise RuntimeError(
        "hub_adapter._call_node is removed. "
        "Use forge.adapters.hub_client.HubClient.invoke()."
    )


def get_repo_context(project_path: str) -> RepoContext:
    _warn_deprecated("get_repo_context")
    from forge.adapters.repo import get_repo_context as _via_client

    return _via_client(project_path)


def check_constitution(target: str, old_text: str, new_text: str) -> ConstitutionResult:
    _warn_deprecated("check_constitution")
    client = HubClient(project_root=".")
    resp = client.invoke(
        capability="lu",
        action="check",
        payload={"target": target, "old_text": old_text, "new_text": new_text},
    )
    if not resp.ok:
        return ConstitutionResult(
            status=CheckStatus.FAIL,
            violations=[ConstitutionViolation(rule_id="hub.lu", message=resp.error)],
            checked_rules=["hub.lu"],
        )
    data = resp.data
    if data.get("passed") or data.get("status") == "pass" or data.get("decision") == "ALLOW":
        return ConstitutionResult(
            status=CheckStatus.PASS,
            checked_rules=list(data.get("checked_rules") or data.get("rule_ids") or ["lu"]),
        )
    return ConstitutionResult(
        status=CheckStatus.FAIL,
        violations=[
            ConstitutionViolation(
                rule_id="lu",
                message=data.get("error")
                or data.get("evidence")
                or "; ".join(data.get("violations") or []),
            )
        ],
        checked_rules=list(data.get("checked_rules") or data.get("rule_ids") or ["lu"]),
    )


def lu_patch(
    target: str,
    old_text: str,
    new_text: str,
    start_line: Optional[int] = None,
    end_line: Optional[int] = None,
) -> bool:
    """Write via Hub/Lu is forbidden. Always raises."""
    raise RuntimeError(
        "hub_adapter.lu_patch write path removed. "
        "Host mutation: Intent → Veritas → Projection only."
    )


def lu_create(target: str, content: str) -> bool:
    raise RuntimeError(
        "hub_adapter.lu_create write path removed. "
        "Host mutation: Intent → Veritas → Projection only."
    )


def lu_delete(target: str) -> bool:
    raise RuntimeError(
        "hub_adapter.lu_delete write path removed. "
        "Host mutation: Intent → Veritas → Projection only."
    )


def run_verification(
    changed_files: List[str], change_type: str = "modify"
) -> VerificationResult:
    _warn_deprecated("run_verification")
    from forge.adapters.verification import verify
    from forge.protocols.models import VerificationRequest

    return verify(
        VerificationRequest(changed_files=list(changed_files), change_type=change_type)
    )

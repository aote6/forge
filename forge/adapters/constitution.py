"""Constitution adapter — protocol in/out; Hub invokes lu rules."""
from __future__ import annotations

from forge.adapters.hub_client import HubClient
from forge.protocols.models import (
    ChangeProposal,
    CheckStatus,
    ConstitutionResult,
    ConstitutionViolation,
)


def check(proposal: ChangeProposal, project_root: str = ".", hub: HubClient | None = None) -> ConstitutionResult:
    """Check ChangeProposal. Never accepts bare dict."""
    if not isinstance(proposal, ChangeProposal):
        raise TypeError("constitution.check requires ChangeProposal, not bare dict")

    # Content-less proposals cannot claim content-level PASS
    has_content = False
    for op in proposal.operations:
        if op.get("content") or op.get("old_text") or op.get("new_text") or op.get("old") or op.get("new"):
            has_content = True
            break

    if not has_content:
        return ConstitutionResult(
            status=CheckStatus.FAIL,
            violations=[
                ConstitutionViolation(
                    rule_id="forge.content_required",
                    message="ChangeProposal lacks content/old_text/new_text; constitution cannot PASS",
                )
            ],
            checked_rules=["forge.content_required"],
        )

    client = hub or HubClient(project_root=project_root)
    resp = client.invoke(
        capability="lu",
        action="constitution_check",
        payload={"proposal": proposal.to_dict()},
    )

    if not resp.ok:
        return ConstitutionResult(
            status=CheckStatus.FAIL,
            violations=[ConstitutionViolation(rule_id="hub.lu", message=resp.error)],
            checked_rules=["hub.lu"],
        )

    data = resp.data if isinstance(resp.data, dict) else {}

    # Case 1: old protocol format (has "passed")
    if "passed" in data:
        if data["passed"] is False or data["passed"] == False:
            viols = [
                ConstitutionViolation(rule_id="hub.lu", message=str(v))
                for v in (data.get("violations") or [])
            ]
            return ConstitutionResult(
                status=CheckStatus.FAIL,
                violations=viols,
                checked_rules=["hub.lu"],
            )
        return ConstitutionResult(
            status=CheckStatus.PASS,
            violations=[],
            checked_rules=list(data.get("checked_rules") or ["hub.lu"]),
        )

    # Case 2: new protocol format (has "status")
    if "status" in data:
        status_raw = data["status"]
        try:
            status = CheckStatus(status_raw)
        except ValueError:
            status = CheckStatus.FAIL

        viols = [
            ConstitutionViolation(rule_id=v.get("rule_id", "?"), message=v.get("message", ""))
            for v in (data.get("violations") or [])
            if isinstance(v, dict)
        ]
        return ConstitutionResult(
            status=status,
            violations=viols,
            checked_rules=list(data.get("checked_rules") or ["lu"]),
        )

    # Case 3: unrecognized response format — fail-closed
    return ConstitutionResult(
        status=CheckStatus.FAIL,
        violations=[
            ConstitutionViolation(
                rule_id="forge.unrecognized_response",
                message="Received unrecognized Hub response format; constitution check cannot PASS",
            )
        ],
        checked_rules=["forge.unrecognized_response"],
    )

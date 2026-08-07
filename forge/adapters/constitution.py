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

    client = hub or HubClient(project_root=project_root)
    resp = client.invoke(
        capability="lu",
        action="constitution_check",
        payload={"proposal": proposal.to_dict()},
    )

    if not resp.ok:
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
        return ConstitutionResult(
            status=CheckStatus.FAIL,
            violations=[ConstitutionViolation(rule_id="hub.lu", message=resp.error)],
            checked_rules=["hub.lu"],
        )

    data = resp.data
    status_raw = data.get("status", "pass")
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

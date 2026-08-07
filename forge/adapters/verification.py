"""Verification adapter — protocol in/out; Hub invokes sms."""
from __future__ import annotations

from forge.adapters.hub_client import HubClient
from forge.protocols.models import CheckStatus, VerificationRequest, VerificationResult


def verify(request: VerificationRequest, project_root: str = ".", hub: HubClient | None = None) -> VerificationResult:
    if not isinstance(request, VerificationRequest):
        raise TypeError("verification.verify requires VerificationRequest")

    client = hub or HubClient(project_root=project_root)
    resp = client.invoke(
        capability="sms",
        action="verify",
        payload=request.to_dict(),
        timeout=120,
    )
    if not resp.ok:
        return VerificationResult(
            status=CheckStatus.FAIL,
            executed_checks=["hub.sms"],
            failures=[resp.error or "sms unavailable"],
        )

    data = resp.data
    status_raw = data.get("status", "pass")
    try:
        status = CheckStatus(status_raw)
    except ValueError:
        status = CheckStatus.FAIL
    return VerificationResult(
        status=status,
        executed_checks=list(data.get("executed_checks") or ["sms"]),
        failures=list(data.get("failures") or []),
    )

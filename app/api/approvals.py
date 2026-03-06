"""Approval API handlers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.services.policy_engine import ActionPolicyEngine, ApprovalDecision, PolicyError


DECISION_ROUTE = "/api/v1/approvals/:id/decision"


@dataclass(frozen=True)
class ApiResponse:
    """Lightweight API response object for HTTP adapters."""

    status_code: int
    body: dict[str, Any]


def post_approval_decision(
    *,
    approval_id: str,
    payload: dict[str, Any],
    policy_engine: ActionPolicyEngine,
) -> ApiResponse:
    """Handles POST /api/v1/approvals/:id/decision requests."""

    actor = str(payload.get("actor", "")).strip()
    decision_raw = str(payload.get("decision", "")).strip().lower()
    if not actor:
        return ApiResponse(status_code=400, body={"error": "actor is required"})

    try:
        decision = ApprovalDecision(decision_raw)
    except ValueError:
        return ApiResponse(
            status_code=400,
            body={"error": "decision must be one of: approved, rejected"},
        )

    try:
        approval = policy_engine.record_approval_decision(
            approval_id=approval_id,
            actor=actor,
            decision=decision,
        )
    except PolicyError as exc:
        return ApiResponse(status_code=404, body={"error": str(exc)})

    return ApiResponse(
        status_code=200,
        body={
            "approval_id": approval.approval_id,
            "action_id": approval.action_id,
            "action_type": approval.action_type,
            "status": approval.status.value,
            "decided_by": approval.decided_by,
            "decided_at": approval.decided_at,
        },
    )

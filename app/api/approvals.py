"""Approval API handlers.

Route integration notes:
- `post_approval_decision` expects authentication/authorization to be performed by
  upstream HTTP middleware.
- Middleware must provide an `AuthenticatedActorContext` instance separately from
  the JSON payload; user-controlled payload data is never trusted for auth.
- The payload `actor` must match the trusted context subject.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.services.policy_engine import (
    ActionPolicyEngine,
    ApprovalDecision,
    ApprovalStatus,
    PolicyError,
)


DECISION_ROUTE = "/api/v1/approvals/:id/decision"

APPROVAL_TRANSITION_GRAPH: dict[ApprovalStatus, frozenset[ApprovalStatus]] = {
    ApprovalStatus.PENDING: frozenset({ApprovalStatus.APPROVED, ApprovalStatus.REJECTED}),
    ApprovalStatus.APPROVED: frozenset(),
    ApprovalStatus.REJECTED: frozenset(),
}

_DECISION_TO_STATUS = {
    ApprovalDecision.APPROVED: ApprovalStatus.APPROVED,
    ApprovalDecision.REJECTED: ApprovalStatus.REJECTED,
}

APPROVAL_DECISION_PERMISSION = "approvals:decide"


@dataclass(frozen=True)
class ApiResponse:
    """Lightweight API response object for HTTP adapters."""

    status_code: int
    body: dict[str, Any]


@dataclass(frozen=True)
class AuthenticatedActorContext:
    """Trusted authentication context built by HTTP middleware."""

    subject: str
    authenticated: bool
    permissions: frozenset[str] = frozenset()


def is_valid_approval_transition(*, from_status: ApprovalStatus, to_status: ApprovalStatus) -> bool:
    """Returns whether the requested approval status transition is allowed."""

    return to_status in APPROVAL_TRANSITION_GRAPH.get(from_status, frozenset())


def _authorize_mutation(context: AuthenticatedActorContext, actor: str) -> ApiResponse | None:
    if not context.authenticated:
        return ApiResponse(status_code=401, body={"error": "authentication required"})
    if APPROVAL_DECISION_PERMISSION not in context.permissions:
        return ApiResponse(status_code=403, body={"error": "missing approval decision permission"})

    subject = context.subject.strip()
    if subject and subject != actor:
        return ApiResponse(status_code=403, body={"error": "actor does not match authenticated subject"})
    return None


def post_approval_decision(
    *,
    approval_id: str,
    payload: dict[str, Any],
    authenticated_actor: AuthenticatedActorContext,
    policy_engine: ActionPolicyEngine,
) -> ApiResponse:
    """Handles POST /api/v1/approvals/:id/decision requests."""

    actor = str(payload.get("actor", "")).strip()
    decision_raw = str(payload.get("decision", "")).strip().lower()
    if not actor:
        return ApiResponse(status_code=400, body={"error": "actor is required"})

    authz_error = _authorize_mutation(authenticated_actor, actor)
    if authz_error is not None:
        return authz_error

    try:
        decision = ApprovalDecision(decision_raw)
    except ValueError:
        return ApiResponse(
            status_code=400,
            body={"error": "decision must be one of: approved, rejected"},
        )

    approval = policy_engine.approvals.get(approval_id)
    if approval is None:
        return ApiResponse(status_code=404, body={"error": f"approval '{approval_id}' not found"})

    to_status = _DECISION_TO_STATUS[decision]
    if not is_valid_approval_transition(from_status=approval.status, to_status=to_status):
        return ApiResponse(
            status_code=409,
            body={
                "error": f"invalid transition '{approval.status.value}' -> '{to_status.value}'",
                "from_status": approval.status.value,
                "to_status": to_status.value,
            },
        )

    try:
        approval = policy_engine.record_approval_decision(approval_id=approval_id, actor=actor, decision=decision)
    except PolicyError as exc:
        return ApiResponse(status_code=409, body={"error": str(exc)})

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


__all__ = [
    "APPROVAL_DECISION_PERMISSION",
    "APPROVAL_TRANSITION_GRAPH",
    "AuthenticatedActorContext",
    "ApiResponse",
    "DECISION_ROUTE",
    "is_valid_approval_transition",
    "post_approval_decision",
]

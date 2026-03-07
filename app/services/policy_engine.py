"""Risk-based action policy engine with approval and audit controls."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum


class PolicyError(RuntimeError):
    """Raised when policy actions or approval operations are invalid."""


class RiskLevel(str, Enum):
    """Risk classes used by the policy engine."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ExecutionPolicy(str, Enum):
    """Execution policy selected from action risk."""

    AUTO_EXECUTE = "auto_execute"
    AUTO_EXECUTE_WITH_AUDIT = "auto_execute_with_audit"
    REQUIRE_EXPLICIT_APPROVAL = "require_explicit_approval"


class ApprovalDecision(str, Enum):
    """Allowed human approval decisions."""

    APPROVED = "approved"
    REJECTED = "rejected"


class ApprovalStatus(str, Enum):
    """State machine for approval requests."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


@dataclass(frozen=True)
class ActionRequest:
    """Action submitted for risk classification and execution policy selection."""

    action_id: str
    action_type: str
    description: str


@dataclass(frozen=True)
class PolicyEvaluation:
    """Policy engine output describing execution behavior."""

    action_id: str
    action_type: str
    risk_level: RiskLevel
    execution_policy: ExecutionPolicy
    requires_audit: bool
    requires_explicit_approval: bool


@dataclass
class ApprovalRequest:
    """Approval record created for high-risk actions."""

    approval_id: str
    action_id: str
    action_type: str
    status: ApprovalStatus
    created_at: str
    decided_at: str | None = None
    decided_by: str | None = None


@dataclass(frozen=True)
class AuditTrailRecord:
    """Immutable audit entry for medium/high risk decisions."""

    actor: str
    decision: str
    timestamp: str
    affected_action: str
    approval_id: str | None = None


@dataclass(frozen=True)
class PolicyExecutionResult:
    """Result returned by policy engine once action policy is enforced."""

    evaluation: PolicyEvaluation
    executed: bool
    approval_id: str | None
    blocked: bool = False
    block_reason: str | None = None


class ActionPolicyEngine:
    """Applies risk classification and enforcement for action execution."""

    _RISK_BY_ACTION_TYPE = {
        "internal_research": RiskLevel.LOW,
        "summarization": RiskLevel.LOW,
        "user_thread_message": RiskLevel.MEDIUM,
        "drive_write_user_scope": RiskLevel.MEDIUM,
        "external_sharing": RiskLevel.HIGH,
        "destructive_operation": RiskLevel.HIGH,
        "purchase": RiskLevel.HIGH,
        "credential_change": RiskLevel.HIGH,
    }

    def __init__(self) -> None:
        self.approvals: dict[str, ApprovalRequest] = {}
        self.audit_trail: list[AuditTrailRecord] = []

    def evaluate(self, action: ActionRequest) -> PolicyEvaluation:
        """Maps action type to risk class and execution policy."""

        risk = self._classify_risk(action.action_type)
        if risk is RiskLevel.LOW:
            execution_policy = ExecutionPolicy.AUTO_EXECUTE
        elif risk is RiskLevel.MEDIUM:
            execution_policy = ExecutionPolicy.AUTO_EXECUTE_WITH_AUDIT
        else:
            execution_policy = ExecutionPolicy.REQUIRE_EXPLICIT_APPROVAL

        return PolicyEvaluation(
            action_id=action.action_id,
            action_type=action.action_type,
            risk_level=risk,
            execution_policy=execution_policy,
            requires_audit=execution_policy is ExecutionPolicy.AUTO_EXECUTE_WITH_AUDIT,
            requires_explicit_approval=execution_policy is ExecutionPolicy.REQUIRE_EXPLICIT_APPROVAL,
        )

    def enforce(self, action: ActionRequest) -> PolicyExecutionResult:
        """Executes policy behavior for low/medium/high risk actions."""

        evaluation = self.evaluate(action)
        # Deterministic precedence: auto execute < auto execute with audit < explicit approval.
        if evaluation.execution_policy is ExecutionPolicy.AUTO_EXECUTE:
            return PolicyExecutionResult(evaluation=evaluation, executed=True, approval_id=None)

        if evaluation.execution_policy is ExecutionPolicy.AUTO_EXECUTE_WITH_AUDIT:
            self._record_audit(
                actor="policy-engine",
                decision="auto-executed",
                affected_action=action.action_id,
            )
            return PolicyExecutionResult(evaluation=evaluation, executed=True, approval_id=None)

        approval_id = f"approval-{len(self.approvals) + 1}"
        self.approvals[approval_id] = ApprovalRequest(
            approval_id=approval_id,
            action_id=action.action_id,
            action_type=action.action_type,
            status=ApprovalStatus.PENDING,
            created_at=self._utc_now(),
        )
        return PolicyExecutionResult(
            evaluation=evaluation,
            executed=False,
            approval_id=approval_id,
            blocked=True,
            block_reason="explicit approval required",
        )

    def record_approval_decision(self, *, approval_id: str, actor: str, decision: ApprovalDecision) -> ApprovalRequest:
        """Applies approval decision and emits audit trail record."""

        approval = self.approvals.get(approval_id)
        if approval is None:
            raise PolicyError(f"approval '{approval_id}' not found")
        if approval.status is not ApprovalStatus.PENDING:
            raise PolicyError(f"approval '{approval_id}' already decided")

        approval.status = ApprovalStatus.APPROVED if decision is ApprovalDecision.APPROVED else ApprovalStatus.REJECTED
        approval.decided_by = actor
        approval.decided_at = self._utc_now()

        self._record_audit(
            actor=actor,
            decision=decision.value,
            affected_action=approval.action_id,
            approval_id=approval.approval_id,
            timestamp=approval.decided_at,
        )
        return approval

    def _classify_risk(self, action_type: str) -> RiskLevel:
        normalized = action_type.strip().lower()
        if not normalized:
            # Missing action type falls back to medium so we keep auditability by default.
            return RiskLevel.MEDIUM

        risk = self._RISK_BY_ACTION_TYPE.get(normalized)
        if risk is None:
            # Unknown inputs are treated as high risk to enforce a conservative hard block.
            return RiskLevel.HIGH
        return risk

    def _record_audit(
        self,
        *,
        actor: str,
        decision: str,
        affected_action: str,
        approval_id: str | None = None,
        timestamp: str | None = None,
    ) -> None:
        self.audit_trail.append(
            AuditTrailRecord(
                actor=actor,
                decision=decision,
                timestamp=timestamp or self._utc_now(),
                affected_action=affected_action,
                approval_id=approval_id,
            )
        )

    def _utc_now(self) -> str:
        return datetime.now(UTC).isoformat()

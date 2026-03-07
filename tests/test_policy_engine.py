import unittest

from app.services.policy_engine import (
    ActionPolicyEngine,
    ActionRequest,
    ApprovalDecision,
    ApprovalStatus,
    ExecutionPolicy,
    RiskLevel,
)


class ActionPolicyEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = ActionPolicyEngine()

    def test_low_risk_actions_auto_execute_without_audit(self):
        result = self.engine.enforce(
            ActionRequest(action_id="act-1", action_type="internal_research", description="research")
        )

        self.assertTrue(result.executed)
        self.assertEqual(result.evaluation.risk_level, RiskLevel.LOW)
        self.assertEqual(result.evaluation.execution_policy, ExecutionPolicy.AUTO_EXECUTE)
        self.assertEqual(result.approval_id, None)
        self.assertEqual(self.engine.audit_trail, [])

    def test_medium_risk_actions_auto_execute_and_write_audit(self):
        result = self.engine.enforce(
            ActionRequest(action_id="act-2", action_type="user_thread_message", description="reply to user")
        )

        self.assertTrue(result.executed)
        self.assertEqual(result.evaluation.risk_level, RiskLevel.MEDIUM)
        self.assertEqual(result.evaluation.execution_policy, ExecutionPolicy.AUTO_EXECUTE_WITH_AUDIT)
        self.assertEqual(len(self.engine.audit_trail), 1)
        audit = self.engine.audit_trail[0]
        self.assertEqual(audit.actor, "policy-engine")
        self.assertEqual(audit.decision, "auto-executed")
        self.assertEqual(audit.affected_action, "act-2")

    def test_high_risk_actions_require_explicit_approval(self):
        result = self.engine.enforce(
            ActionRequest(action_id="act-3", action_type="external_sharing", description="share externally")
        )

        self.assertFalse(result.executed)
        self.assertEqual(result.evaluation.risk_level, RiskLevel.HIGH)
        self.assertEqual(result.evaluation.execution_policy, ExecutionPolicy.REQUIRE_EXPLICIT_APPROVAL)
        self.assertIsNotNone(result.approval_id)
        approval = self.engine.approvals[result.approval_id]
        self.assertEqual(approval.status, ApprovalStatus.PENDING)

    def test_conflicting_action_type_normalization_uses_deterministic_winner(self):
        result = self.engine.enforce(
            ActionRequest(
                action_id="act-5",
                action_type="  EXTERNAL_SHARING  ",
                description="share with external system",
            )
        )

        self.assertEqual(result.evaluation.risk_level, RiskLevel.HIGH)
        self.assertEqual(result.evaluation.execution_policy, ExecutionPolicy.REQUIRE_EXPLICIT_APPROVAL)
        self.assertFalse(result.executed)
        self.assertTrue(result.blocked)
        self.assertEqual(result.block_reason, "explicit approval required")

    def test_missing_action_type_falls_back_to_medium_with_audit(self):
        result = self.engine.enforce(
            ActionRequest(action_id="act-6", action_type="   ", description="missing type")
        )

        self.assertTrue(result.executed)
        self.assertEqual(result.evaluation.risk_level, RiskLevel.MEDIUM)
        self.assertEqual(result.evaluation.execution_policy, ExecutionPolicy.AUTO_EXECUTE_WITH_AUDIT)
        self.assertEqual(len(self.engine.audit_trail), 1)

    def test_unknown_action_type_is_hard_blocked_with_explicit_outcome(self):
        result = self.engine.enforce(
            ActionRequest(action_id="act-7", action_type="unknown_capability", description="unknown")
        )

        self.assertFalse(result.executed)
        self.assertTrue(result.blocked)
        self.assertEqual(result.block_reason, "explicit approval required")
        self.assertEqual(result.evaluation.risk_level, RiskLevel.HIGH)
        self.assertIsNotNone(result.approval_id)

    def test_approval_decision_records_audit_trail(self):
        result = self.engine.enforce(
            ActionRequest(action_id="act-4", action_type="purchase", description="buy")
        )

        approval = self.engine.record_approval_decision(
            approval_id=result.approval_id,
            actor="manager-1",
            decision=ApprovalDecision.APPROVED,
        )

        self.assertEqual(approval.status, ApprovalStatus.APPROVED)
        self.assertEqual(approval.decided_by, "manager-1")
        self.assertEqual(len(self.engine.audit_trail), 1)
        audit = self.engine.audit_trail[0]
        self.assertEqual(audit.actor, "manager-1")
        self.assertEqual(audit.decision, "approved")
        self.assertEqual(audit.affected_action, "act-4")
        self.assertEqual(audit.approval_id, result.approval_id)


if __name__ == "__main__":
    unittest.main()

import unittest

from app.api.approvals import DECISION_ROUTE, is_valid_approval_transition, post_approval_decision
from app.services.policy_engine import ActionPolicyEngine, ActionRequest, ApprovalStatus


class ApprovalsApiTests(unittest.TestCase):
    @staticmethod
    def _authorized_payload(*, actor: str, decision: str, can_decide_approvals: bool = True) -> dict[str, object]:
        return {
            "actor": actor,
            "decision": decision,
            "auth": {
                "authenticated": True,
                "subject": actor,
                "can_decide_approvals": can_decide_approvals,
            },
        }

    def test_decision_route_constant_matches_contract(self):
        self.assertEqual(DECISION_ROUTE, "/api/v1/approvals/:id/decision")

    def test_transition_graph_enforces_terminal_states(self):
        self.assertTrue(
            is_valid_approval_transition(from_status=ApprovalStatus.PENDING, to_status=ApprovalStatus.APPROVED)
        )
        self.assertTrue(
            is_valid_approval_transition(from_status=ApprovalStatus.PENDING, to_status=ApprovalStatus.REJECTED)
        )
        self.assertFalse(
            is_valid_approval_transition(from_status=ApprovalStatus.APPROVED, to_status=ApprovalStatus.PENDING)
        )
        self.assertFalse(
            is_valid_approval_transition(from_status=ApprovalStatus.REJECTED, to_status=ApprovalStatus.PENDING)
        )

    def test_post_approval_decision_requires_authentication(self):
        engine = ActionPolicyEngine()
        result = engine.enforce(
            ActionRequest(action_id="act-12", action_type="credential_change", description="rotate key")
        )

        response = post_approval_decision(
            approval_id=result.approval_id or "",
            payload={"actor": "admin", "decision": "approved"},
            policy_engine=engine,
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.body["error"], "authentication required")

    def test_post_approval_decision_requires_approval_permission(self):
        engine = ActionPolicyEngine()
        result = engine.enforce(
            ActionRequest(action_id="act-13", action_type="credential_change", description="rotate key")
        )

        response = post_approval_decision(
            approval_id=result.approval_id or "",
            payload=self._authorized_payload(actor="admin", decision="approved", can_decide_approvals=False),
            policy_engine=engine,
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.body["error"], "missing approval decision permission")

    def test_post_approval_decision_forbids_actor_subject_mismatch(self):
        engine = ActionPolicyEngine()
        result = engine.enforce(
            ActionRequest(action_id="act-14", action_type="credential_change", description="rotate key")
        )

        response = post_approval_decision(
            approval_id=result.approval_id or "",
            payload={
                "actor": "admin",
                "decision": "approved",
                "auth": {
                    "authenticated": True,
                    "subject": "other-admin",
                    "can_decide_approvals": True,
                },
            },
            policy_engine=engine,
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.body["error"], "actor does not match authenticated subject")

    def test_post_approval_decision_updates_approval_and_audits(self):
        engine = ActionPolicyEngine()
        result = engine.enforce(
            ActionRequest(action_id="act-10", action_type="credential_change", description="rotate key")
        )

        response = post_approval_decision(
            approval_id=result.approval_id,
            payload=self._authorized_payload(actor="admin", decision="approved"),
            policy_engine=engine,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.body["approval_id"], result.approval_id)
        self.assertEqual(response.body["action_id"], "act-10")
        self.assertEqual(response.body["status"], "approved")
        self.assertEqual(response.body["decided_by"], "admin")
        self.assertEqual(len(engine.audit_trail), 1)
        self.assertEqual(engine.audit_trail[0].actor, "admin")
        self.assertEqual(engine.audit_trail[0].decision, "approved")

    def test_post_approval_decision_rejects_invalid_payload(self):
        engine = ActionPolicyEngine()

        response = post_approval_decision(
            approval_id="approval-404",
            payload={"actor": "", "decision": "allow"},
            policy_engine=engine,
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("error", response.body)

    def test_post_approval_decision_rejects_duplicate_approval(self):
        engine = ActionPolicyEngine()
        result = engine.enforce(
            ActionRequest(action_id="act-15", action_type="credential_change", description="rotate key")
        )

        first_response = post_approval_decision(
            approval_id=result.approval_id or "",
            payload=self._authorized_payload(actor="admin", decision="approved"),
            policy_engine=engine,
        )
        duplicate_response = post_approval_decision(
            approval_id=result.approval_id or "",
            payload=self._authorized_payload(actor="admin", decision="approved"),
            policy_engine=engine,
        )

        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(duplicate_response.status_code, 409)
        self.assertEqual(duplicate_response.body["from_status"], "approved")
        self.assertEqual(duplicate_response.body["to_status"], "approved")
        self.assertEqual(len(engine.audit_trail), 1)

    def test_post_approval_decision_rejects_duplicate_rejection(self):
        engine = ActionPolicyEngine()
        result = engine.enforce(
            ActionRequest(action_id="act-16", action_type="credential_change", description="rotate key")
        )

        first_response = post_approval_decision(
            approval_id=result.approval_id or "",
            payload=self._authorized_payload(actor="admin", decision="rejected"),
            policy_engine=engine,
        )
        duplicate_response = post_approval_decision(
            approval_id=result.approval_id or "",
            payload=self._authorized_payload(actor="admin", decision="rejected"),
            policy_engine=engine,
        )

        self.assertEqual(first_response.status_code, 200)
        self.assertEqual(duplicate_response.status_code, 409)
        self.assertEqual(duplicate_response.body["from_status"], "rejected")
        self.assertEqual(duplicate_response.body["to_status"], "rejected")
        self.assertEqual(len(engine.audit_trail), 1)


if __name__ == "__main__":
    unittest.main()

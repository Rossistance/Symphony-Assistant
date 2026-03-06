import unittest

from app.api.approvals import DECISION_ROUTE, post_approval_decision
from app.services.policy_engine import ActionPolicyEngine, ActionRequest


class ApprovalsApiTests(unittest.TestCase):
    def test_decision_route_constant_matches_contract(self):
        self.assertEqual(DECISION_ROUTE, "/api/v1/approvals/:id/decision")

    def test_post_approval_decision_updates_approval_and_audits(self):
        engine = ActionPolicyEngine()
        result = engine.enforce(
            ActionRequest(action_id="act-10", action_type="credential_change", description="rotate key")
        )

        response = post_approval_decision(
            approval_id=result.approval_id,
            payload={"actor": "admin", "decision": "approved"},
            policy_engine=engine,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.body["approval_id"], result.approval_id)
        self.assertEqual(response.body["action_id"], "act-10")
        self.assertEqual(response.body["status"], "approved")
        self.assertEqual(response.body["decided_by"], "admin")
        self.assertEqual(len(engine.audit_trail), 1)
        self.assertEqual(engine.audit_trail[0].actor, "admin")

    def test_post_approval_decision_rejects_invalid_payload(self):
        engine = ActionPolicyEngine()

        response = post_approval_decision(
            approval_id="approval-404",
            payload={"actor": "", "decision": "allow"},
            policy_engine=engine,
        )

        self.assertEqual(response.status_code, 400)
        self.assertIn("error", response.body)


if __name__ == "__main__":
    unittest.main()

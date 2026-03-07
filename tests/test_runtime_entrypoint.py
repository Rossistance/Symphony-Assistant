from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from app.main import RuntimeContainer, create_app, wire_runtime_dependencies
from app.services.policy_engine import ActionRequest


class RuntimeEntrypointIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        os.environ["HTTP_SURFACE_STATE_DB_PATH"] = str(Path(self.temp_dir.name) / "http_surface_state.db")
        os.environ["MESSAGING_STATE_DB_PATH"] = str(Path(self.temp_dir.name) / "messaging_state.db")

        runtime = wire_runtime_dependencies()
        self.runtime = RuntimeContainer(http_handlers=runtime.http_handlers, policy_engine=runtime.policy_engine)
        self.app = create_app(runtime=self.runtime)
        self.client = self.app.test_client()

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_routes_registered_and_daily_suggestion_returns_json_shape(self):
        routes = {rule.rule for rule in self.app.url_map.iter_rules()}
        self.assertIn("/webhooks/whatsapp", routes)
        self.assertIn("/jobs/orchestrate", routes)
        self.assertIn("/jobs/daily-suggestion", routes)
        self.assertIn("/api/v1/tasks/<task_id>", routes)
        self.assertIn("/api/v1/approvals/<approval_id>/decision", routes)

        response = self.client.post("/jobs/daily-suggestion", json={"user": "alex"})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content_type, "application/json")
        body = response.get_json()
        self.assertEqual(body["status"], "completed")
        self.assertIn("task_id", body)
        self.assertIn("deliverables", body)

    def test_approval_decision_route_end_to_end_json_response(self):
        approval = self.runtime.policy_engine.enforce(
            ActionRequest(action_id="act-1", action_type="credential_change", description="rotate keys")
        )
        approval_id = approval.approval_id or ""

        response = self.client.post(
            f"/api/v1/approvals/{approval_id}/decision",
            json={"actor": "ops", "decision": "approved"},
            headers={
                "X-Actor-Id": "ops",
                "X-Actor-Permissions": "approvals:decide",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content_type, "application/json")
        body = response.get_json()
        self.assertEqual(body["approval_id"], approval_id)
        self.assertEqual(body["status"], "approved")
        self.assertEqual(body["decided_by"], "ops")


if __name__ == "__main__":
    unittest.main()

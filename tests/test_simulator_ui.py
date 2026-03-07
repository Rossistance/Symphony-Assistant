from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from app.api.http_surfaces import HttpSurfaceHandlers
from app.api.simulator import SimulationStateStore
from app.main import RuntimeContainer, create_app
from app.messaging.agent_bus import AgentMessageBroker
from app.messaging.state_store import SqliteHttpSurfaceStateStore
from app.services.agent_runtime import AgentRuntime
from app.services.policy_engine import ActionPolicyEngine


class SimulatorUiTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        db_path = Path(self.temp_dir.name) / "state.db"
        sim_path = Path(self.temp_dir.name) / "sim-state.json"
        state_store = SqliteHttpSurfaceStateStore(db_path)
        runtime = RuntimeContainer(
            http_handlers=HttpSurfaceHandlers(
                runtime=AgentRuntime(clock=lambda: 0),
                message_bus=AgentMessageBroker(),
                tasks=state_store,
                event_store=state_store,
            ),
            policy_engine=ActionPolicyEngine(),
            simulation_store=SimulationStateStore(path=sim_path),
        )
        self.client = create_app(runtime=runtime).test_client()

    def tearDown(self):
        os.environ.pop("GROQ_API_KEY", None)
        self.temp_dir.cleanup()

    def test_simulator_page_renders(self):
        response = self.client.get("/simulator")

        self.assertEqual(response.status_code, 200)
        body = response.get_data(as_text=True)
        self.assertIn("5-window simulation", body)
        self.assertIn("Auto-run full workflow", body)

    def test_simulator_manual_end_to_end_flow(self):
        initialized = self.client.post(
            "/simulator/api/whatsapp-init",
            json={"phone": "+15551230000", "message": "Please process this return.", "auto_process": False},
        )
        self.assertEqual(initialized.status_code, 200)
        self.assertEqual(initialized.get_json()["status"], "initiated")

        ran = self.client.post("/simulator/api/agent-run", json={"use_groq": False})
        self.assertEqual(ran.status_code, 200)
        run_payload = ran.get_json()
        self.assertEqual(run_payload["status"], "awaiting_approval")
        self.assertTrue(run_payload["agent"]["events"])

        approved = self.client.post("/simulator/api/approve", json={"approved": True, "note": "Ship it."})
        self.assertEqual(approved.status_code, 200)
        self.assertEqual(approved.get_json()["approval"]["status"], "approved")

        published = self.client.post("/simulator/api/publish", json={})
        self.assertEqual(published.status_code, 200)
        publish_payload = published.get_json()
        self.assertEqual(publish_payload["status"], "completed")
        self.assertIn("https://drive.example/simulated", publish_payload["return_message"])

    def test_simulator_auto_run_happy_path(self):
        response = self.client.post(
            "/simulator/api/whatsapp-init",
            json={
                "phone": "+15551230000",
                "message": "Auto process this return.",
                "auto_process": True,
                "use_groq": False,
                "require_groq": False,
                "approval_note": "Auto approval.",
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["approval"]["status"], "approved")
        self.assertIn("Your return is ready", payload["return_message"])

    def test_simulator_requires_groq_when_requested(self):
        self.client.post(
            "/simulator/api/whatsapp-init",
            json={"phone": "+15551230000", "message": "Please process this return.", "auto_process": False},
        )

        response = self.client.post("/simulator/api/agent-run", json={"use_groq": True, "require_groq": True})
        self.assertEqual(response.status_code, 400)
        payload = response.get_json()
        self.assertEqual(payload["status"], "failed")
        self.assertIn("GROQ_API_KEY", payload["error"])

    def test_simulator_publish_requires_approval(self):
        self.client.post(
            "/simulator/api/whatsapp-init",
            json={"phone": "+15551230000", "message": "Please process this return.", "auto_process": False},
        )

        response = self.client.post("/simulator/api/publish", json={})
        self.assertEqual(response.status_code, 400)
        self.assertIn("approved", response.get_json()["error"])


if __name__ == "__main__":
    unittest.main()

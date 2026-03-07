from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from app.main import RuntimeContainer, create_app
from app.api.http_surfaces import HttpSurfaceHandlers
from app.api.simulator import SimulationStateStore
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
        self.temp_dir.cleanup()

    def test_simulator_page_renders(self):
        response = self.client.get("/simulator")

        self.assertEqual(response.status_code, 200)
        self.assertIn("5-window simulation", response.get_data(as_text=True))

    def test_simulator_end_to_end_flow(self):
        initialized = self.client.post(
            "/simulator/api/whatsapp-init",
            json={"phone": "+15551230000", "message": "Please process this return."},
        )
        self.assertEqual(initialized.status_code, 200)
        init_payload = initialized.get_json()
        self.assertEqual(init_payload["status"], "initiated")

        ran = self.client.post("/simulator/api/agent-run", json={"use_groq": False})
        self.assertEqual(ran.status_code, 200)
        run_payload = ran.get_json()
        self.assertEqual(run_payload["status"], "awaiting_approval")
        self.assertTrue(run_payload["agent"]["events"])

        approved = self.client.post(
            "/simulator/api/approve",
            json={"approved": True, "note": "Ship it."},
        )
        self.assertEqual(approved.status_code, 200)
        self.assertEqual(approved.get_json()["approval"]["status"], "approved")

        published = self.client.post("/simulator/api/publish", json={})
        self.assertEqual(published.status_code, 200)
        publish_payload = published.get_json()
        self.assertEqual(publish_payload["status"], "completed")
        self.assertIn("https://drive.example/simulated", publish_payload["return_message"])

    def test_simulator_publish_requires_approval(self):
        self.client.post(
            "/simulator/api/whatsapp-init",
            json={"phone": "+15551230000", "message": "Please process this return."},
        )

        response = self.client.post("/simulator/api/publish", json={})
        self.assertEqual(response.status_code, 400)
        self.assertIn("approved", response.get_json()["error"])


if __name__ == "__main__":
    unittest.main()

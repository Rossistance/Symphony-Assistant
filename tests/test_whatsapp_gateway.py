import tempfile
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.gateway.whatsapp_gateway import FileAuthStateStore, GatewaySessionState, WhatsAppGateway


@dataclass
class FakeConnector:
    failures_before_success: int = 0
    connect_calls: int = 0
    disconnect_calls: int = 0
    auth_states_seen: list[dict[str, Any]] = field(default_factory=list)

    def connect(self, auth_state: dict[str, Any]) -> dict[str, Any]:
        self.connect_calls += 1
        self.auth_states_seen.append(dict(auth_state))
        if self.connect_calls <= self.failures_before_success:
            raise RuntimeError(f"failed-attempt-{self.connect_calls}")
        return {"token": f"token-{self.connect_calls}"}

    def disconnect(self) -> None:
        self.disconnect_calls += 1


class WhatsAppGatewayTests(unittest.TestCase):
    def test_connect_loads_and_saves_auth_state(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = FileAuthStateStore(Path(tmpdir) / "auth.json")
            store.save({"token": "old"})
            connector = FakeConnector()
            telemetry_states: list[GatewaySessionState] = []

            gateway = WhatsAppGateway(
                session_id="session-1",
                connector=connector,
                auth_state_store=store,
                telemetry_handler=lambda event: telemetry_states.append(event.state),
            )

            self.assertTrue(gateway.connect())
            self.assertEqual(connector.auth_states_seen[0], {"token": "old"})
            self.assertEqual(store.load(), {"token": "token-1"})
            self.assertEqual(gateway.state, GatewaySessionState.CONNECTED)
            self.assertEqual(
                telemetry_states,
                [GatewaySessionState.CONNECTING, GatewaySessionState.CONNECTED],
            )

    def test_reconnect_uses_exponential_backoff_without_sleeping(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = FileAuthStateStore(Path(tmpdir) / "auth.json")
            connector = FakeConnector(failures_before_success=2)
            delays: list[float] = []

            gateway = WhatsAppGateway(
                session_id="session-2",
                connector=connector,
                auth_state_store=store,
                base_backoff_seconds=1.0,
                max_backoff_seconds=10.0,
                jitter_ratio=0.0,
                sleep_fn=lambda delay: delays.append(delay),
            )

            self.assertTrue(gateway.connect())
            self.assertEqual(delays, [1.0, 2.0])
            self.assertEqual(gateway.state, GatewaySessionState.CONNECTED)

    def test_failed_state_when_retries_exhausted(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = FileAuthStateStore(Path(tmpdir) / "auth.json")
            connector = FakeConnector(failures_before_success=99)

            gateway = WhatsAppGateway(
                session_id="session-3",
                connector=connector,
                auth_state_store=store,
                max_reconnect_attempts=2,
                jitter_ratio=0.0,
                sleep_fn=lambda _: None,
            )

            self.assertFalse(gateway.connect())
            snapshot = gateway.snapshot()
            self.assertEqual(snapshot.state, GatewaySessionState.FAILED)
            self.assertIn("failed-attempt", snapshot.last_error or "")

    def test_disconnect_resets_state_and_calls_connector(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            store = FileAuthStateStore(Path(tmpdir) / "auth.json")
            connector = FakeConnector()

            gateway = WhatsAppGateway(
                session_id="session-4",
                connector=connector,
                auth_state_store=store,
            )
            gateway.connect()
            gateway.disconnect()

            self.assertEqual(connector.disconnect_calls, 1)
            self.assertEqual(gateway.state, GatewaySessionState.DISCONNECTED)


if __name__ == "__main__":
    unittest.main()

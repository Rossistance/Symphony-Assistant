import os
import unittest
from urllib.error import HTTPError, URLError
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from dataclasses import dataclass, field
from typing import Any

from app.config import MessagingConfig
from app.messaging.adapters.whatsapp import WhatsAppCloudAdapter
from app.messaging.base import InboundMessage
from app.messaging.gateway_client import (
    GatewayHTTPError,
    GatewayRetryExhaustedError,
    GatewayTransportError,
    HttpGatewayClient,
)
from app.messaging.router import MessagingRouter
from app.messaging.state_store import SqliteMessagingStateStore
from app.webhooks.inbound import parse_inbound_webhook


@dataclass
class FakeGatewayClient:
    session_id: str | None = "session-test"
    responses: list[dict[str, Any]] = field(default_factory=list)
    sent_payloads: list[dict[str, Any]] = field(default_factory=list)

    def send(self, *, session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        self.sent_payloads.append({"session_id": session_id, **payload})
        if self.responses:
            return self.responses.pop(0)
        return {"message_id": "wamid.default", "thread_id": payload.get("thread_id")}


class MessagingRouterTests(unittest.TestCase):
    def test_default_channel_is_whatsapp(self):
        cfg = MessagingConfig()
        self.assertEqual(cfg.default_channel, os.getenv("DEFAULT_CHANNEL", "whatsapp"))

    def test_route_by_channel_and_fallback_to_sms(self):
        router = MessagingRouter(
            MessagingConfig(default_channel="whatsapp", enable_sms_fallback=True, enable_ios_bridge=False)
        )
        result = router.send_message("+1555", "hello", channel="unknown")
        self.assertTrue(result.provider_message_id.startswith("sms-"))

    def test_reply_uses_original_thread(self):
        gateway = FakeGatewayClient(responses=[{"message_id": "wamid.2", "thread_id": "thread.123"}])
        router = MessagingRouter(
            MessagingConfig(default_channel="whatsapp", enable_sms_fallback=True, enable_ios_bridge=False),
            adapters={"whatsapp": WhatsAppCloudAdapter(gateway_client=gateway)},
        )
        inbound = InboundMessage(
            channel="whatsapp",
            from_user="+1555",
            body="hi",
            provider_message_id="wamid.1",
            provider_thread_id="thread.123",
        )
        reply = router.reply_in_thread(inbound, "pong")
        self.assertEqual(reply.provider_thread_id, "thread.123")
        self.assertEqual(gateway.sent_payloads[0]["in_reply_to"], "wamid.1")

    def test_whatsapp_adapter_uses_provider_ids_from_gateway_response(self):
        gateway = FakeGatewayClient(
            responses=[{"provider_message_id": "provider-5", "provider_thread_id": "conv-7", "status": "queued"}]
        )
        adapter = WhatsAppCloudAdapter(gateway_client=gateway)

        result = adapter.send_message("+1555", "hello", thread_id="conv-1")

        self.assertEqual(result.provider_message_id, "provider-5")
        self.assertEqual(result.provider_thread_id, "conv-7")
        self.assertEqual(result.raw_response["provider_message_id"], "provider-5")
        self.assertEqual(result.raw_response["provider_thread_id"], "conv-7")
        self.assertEqual(result.raw_response["status"], "queued")
        self.assertTrue(result.raw_response["correlation_id"])


    def test_whatsapp_adapter_records_outbound_correlation(self):
        with TemporaryDirectory() as tmp_dir:
            state_store = SqliteMessagingStateStore(path=Path(tmp_dir) / "messaging.db")
            gateway = FakeGatewayClient(responses=[{"provider_message_id": "provider-8", "provider_thread_id": "thread-1"}])
            adapter = WhatsAppCloudAdapter(gateway_client=gateway, correlation_store=state_store)

            result = adapter.send_message("+1555", "hello")

            correlation_id = str(result.raw_response.get("correlation_id"))
            record = state_store.get(correlation_id)
            self.assertIsNotNone(record)
            assert record is not None
            self.assertEqual(record.provider_message_id, "provider-8")
            self.assertEqual(record.provider_thread_id, "thread-1")
            self.assertEqual(record.recipient, "+1555")

    def test_whatsapp_adapter_surfaces_http_errors_with_correlation_id(self):
        class FailingGatewayClient:
            session_id = "session-test"

            def send(self, *, session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
                raise GatewayHTTPError(status_code=400, response_body='{"error":"bad request"}')

        adapter = WhatsAppCloudAdapter(gateway_client=FailingGatewayClient())

        with self.assertRaisesRegex(Exception, "correlation_id=") as ctx:
            adapter.send_message("+1555", "hello")

        exc = ctx.exception
        self.assertFalse(getattr(exc, "retryable", True))
        self.assertTrue(getattr(exc, "correlation_id", ""))

    def test_whatsapp_adapter_preserves_provided_correlation_id_on_failure(self):
        class FailingGatewayClient:
            session_id = "session-test"

            def send(self, *, session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
                raise GatewayTransportError("connection reset")

        adapter = WhatsAppCloudAdapter(gateway_client=FailingGatewayClient())

        with self.assertRaisesRegex(Exception, "correlation_id=corr-123") as ctx:
            adapter._send_via_gateway({"to": "+1555", "type": "text", "text": "hello", "correlation_id": "corr-123"})

        self.assertEqual(getattr(ctx.exception, "correlation_id", None), "corr-123")


class HttpGatewayClientRetryTests(unittest.TestCase):
    def test_retries_transient_transport_failure_then_succeeds(self):
        calls = 0
        delays: list[float] = []

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, tb):
                return False

            def read(self):
                return b'{"message_id":"ok-1"}'

        def flaky_urlopen(*args: Any, **kwargs: Any) -> Any:
            nonlocal calls
            calls += 1
            if calls < 3:
                raise URLError("temporary dns failure")
            return FakeResponse()

        client = HttpGatewayClient(
            base_url="http://localhost:8080",
            api_key=None,
            session_id="session-test",
            max_attempts=3,
            jitter_ratio=0.0,
            sleep_fn=lambda delay: delays.append(delay),
            urlopen_fn=flaky_urlopen,
        )

        response = client.send(session_id="session-test", payload={"to": "+1555"})

        self.assertEqual(response["message_id"], "ok-1")
        self.assertEqual(calls, 3)
        self.assertEqual(delays, [0.25, 0.5])

    def test_does_not_retry_non_retryable_4xx(self):
        calls = 0

        def bad_request(*args: Any, **kwargs: Any) -> Any:
            nonlocal calls
            calls += 1
            raise HTTPError(
                url="http://localhost",
                code=400,
                msg="Bad Request",
                hdrs=None,
                fp=BytesIO(b'{"error":"invalid payload"}'),
            )

        client = HttpGatewayClient(
            base_url="http://localhost:8080",
            api_key=None,
            session_id="session-test",
            max_attempts=3,
            jitter_ratio=0.0,
            sleep_fn=lambda _: None,
            urlopen_fn=bad_request,
        )

        with self.assertRaises(GatewayHTTPError) as ctx:
            client.send(session_id="session-test", payload={"to": "+1555"})

        self.assertEqual(calls, 1)
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIn("invalid payload", str(ctx.exception))

    def test_retries_5xx_and_raises_retry_exhausted(self):
        calls = 0

        def unavailable(*args: Any, **kwargs: Any) -> Any:
            nonlocal calls
            calls += 1
            raise HTTPError(
                url="http://localhost",
                code=503,
                msg="Service Unavailable",
                hdrs=None,
                fp=BytesIO(b'{"error":"overloaded"}'),
            )

        client = HttpGatewayClient(
            base_url="http://localhost:8080",
            api_key=None,
            session_id="session-test",
            max_attempts=3,
            jitter_ratio=0.0,
            sleep_fn=lambda _: None,
            urlopen_fn=unavailable,
        )

        with self.assertRaises(GatewayRetryExhaustedError) as ctx:
            client.send(session_id="session-test", payload={"to": "+1555"})

        self.assertEqual(calls, 3)
        self.assertEqual(ctx.exception.attempts, 3)
        self.assertIsInstance(ctx.exception.last_error, GatewayHTTPError)



class WebhookParserTests(unittest.TestCase):
    def test_provider_message_id_is_trimmed_for_stable_dedup_key(self):
        payload = {
            "message_id": "  msg-42  ",
            "conversation_id": " conv-9 ",
            "from": " +1555 ",
            "body": " hello ",
        }
        inbound = parse_inbound_webhook(payload, channel="sms")
        self.assertEqual(inbound.provider_message_id, "msg-42")
        self.assertEqual(inbound.provider_thread_id, "conv-9")
        self.assertEqual(inbound.metadata["stable_provider_id"], "msg-42")

    def test_preserves_provider_ids(self):
        payload = {
            "MessageSid": "SM123",
            "conversation_id": "conv-9",
            "From": "+1555",
            "Body": "hello",
        }
        inbound = parse_inbound_webhook(payload, channel="sms")
        self.assertEqual(inbound.provider_message_id, "SM123")
        self.assertEqual(inbound.provider_thread_id, "conv-9")


if __name__ == "__main__":
    unittest.main()

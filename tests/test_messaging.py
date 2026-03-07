import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from dataclasses import dataclass, field
from typing import Any

from app.config import MessagingConfig
from app.messaging.adapters.whatsapp import WhatsAppCloudAdapter
from app.messaging.base import InboundMessage
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

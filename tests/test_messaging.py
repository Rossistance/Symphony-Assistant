import os
import unittest

from app.config import MessagingConfig
from app.messaging.base import InboundMessage
from app.messaging.router import MessagingRouter
from app.webhooks.inbound import parse_inbound_webhook


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
        router = MessagingRouter(
            MessagingConfig(default_channel="whatsapp", enable_sms_fallback=True, enable_ios_bridge=False)
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


class WebhookParserTests(unittest.TestCase):
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

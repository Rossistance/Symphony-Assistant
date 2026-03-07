from __future__ import annotations

import unittest

from app.messaging.base import InboundMessage
from app.messaging.inbound_pipeline import InMemoryInboundDedupeStore, InboundIngestionPipeline


class InboundIngestionPipelineTests(unittest.TestCase):
    def test_dedupes_using_provider_message_id(self):
        store = InMemoryInboundDedupeStore()
        pipeline = InboundIngestionPipeline(dedupe_store=store)

        first = pipeline.ingest(
            InboundMessage(
                channel="whatsapp",
                from_user="+1555",
                body="hello",
                provider_message_id="wamid-1",
            ),
            source="webhook.whatsapp",
        )
        duplicate = pipeline.ingest(
            InboundMessage(
                channel="whatsapp",
                from_user="+1555",
                body="hello",
                provider_message_id="wamid-1",
            ),
            source="gateway.socket",
        )

        self.assertTrue(first.accepted)
        self.assertFalse(duplicate.accepted)
        self.assertEqual(first.dedupe_key, "whatsapp:provider_message:wamid-1")
        self.assertEqual(duplicate.dedupe_key, first.dedupe_key)

    def test_falls_back_to_provider_thread_id(self):
        store = InMemoryInboundDedupeStore()
        pipeline = InboundIngestionPipeline(dedupe_store=store)

        result = pipeline.ingest(
            InboundMessage(
                channel="whatsapp",
                from_user="+1555",
                body="hello",
                provider_thread_id="thread-9",
            ),
            source="gateway.socket",
        )

        self.assertEqual(result.dedupe_key, "whatsapp:provider_thread:thread-9")


if __name__ == "__main__":
    unittest.main()

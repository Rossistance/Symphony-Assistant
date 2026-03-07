from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from app.messaging.base import InboundMessage
from app.messaging.inbound_pipeline import (
    FileInboundDedupeStore,
    InMemoryInboundDedupeStore,
    InboundIngestionPipeline,
)


class _CleanupCapableStore(InMemoryInboundDedupeStore):
    def __init__(self) -> None:
        super().__init__()
        self.cleanup_calls: list[float | None] = []

    def cleanup_inbound_dedupe_keys(self, *, retention_seconds: float | None = None) -> int:
        self.cleanup_calls.append(retention_seconds)
        return 0


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

    def test_requires_provider_identifier_for_dedupe_keys(self):
        store = InMemoryInboundDedupeStore()
        pipeline = InboundIngestionPipeline(dedupe_store=store)

        with self.assertRaises(ValueError):
            pipeline.ingest(
                InboundMessage(channel="whatsapp", from_user="+1555", body="hello"),
                source="gateway.socket",
            )


    def test_scopes_dedupe_key_with_session_and_account_metadata(self):
        store = InMemoryInboundDedupeStore()
        pipeline = InboundIngestionPipeline(dedupe_store=store)

        first = pipeline.ingest(
            InboundMessage(
                channel="whatsapp",
                from_user="+1555",
                body="hello",
                provider_message_id="wamid-1",
                metadata={"session_id": "session-1", "account_id": "acct-1"},
            ),
            source="webhook.whatsapp",
        )
        second_scope = pipeline.ingest(
            InboundMessage(
                channel="whatsapp",
                from_user="+1555",
                body="hello",
                provider_message_id="wamid-1",
                metadata={"session_id": "session-2", "account_id": "acct-1"},
            ),
            source="webhook.whatsapp",
        )

        self.assertTrue(first.accepted)
        self.assertTrue(second_scope.accepted)
        self.assertEqual(
            first.dedupe_key,
            "whatsapp:session:session-1:account:acct-1:provider_message:wamid-1",
        )
        self.assertEqual(
            second_scope.dedupe_key,
            "whatsapp:session:session-2:account:acct-1:provider_message:wamid-1",
        )

    def test_runs_cleanup_on_startup_and_periodic_ingestion(self):
        store = _CleanupCapableStore()
        pipeline = InboundIngestionPipeline(
            dedupe_store=store,
            dedupe_retention_seconds=45.0,
            dedupe_cleanup_interval_seconds=0,
        )

        pipeline.ingest(
            InboundMessage(
                channel="whatsapp",
                from_user="+1555",
                body="hello",
                provider_message_id="wamid-1",
            ),
            source="webhook.whatsapp",
        )

        self.assertGreaterEqual(len(store.cleanup_calls), 2)
        self.assertEqual(store.cleanup_calls[0], 45.0)
        self.assertEqual(store.cleanup_calls[1], 45.0)

    def test_file_store_persists_dedupe_state(self):
        with TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "dedupe.json"
            first_store = FileInboundDedupeStore(path=path)
            second_store = FileInboundDedupeStore(path=path)

            first_pipeline = InboundIngestionPipeline(dedupe_store=first_store)
            second_pipeline = InboundIngestionPipeline(dedupe_store=second_store)

            first = first_pipeline.ingest(
                InboundMessage(
                    channel="whatsapp",
                    from_user="+1555",
                    body="hello",
                    provider_message_id="wamid-1",
                ),
                source="webhook.whatsapp",
            )
            duplicate = second_pipeline.ingest(
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


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from app.messaging.state_store import OutboundCorrelationRecord, SqliteMessagingStateStore


class SqliteMessagingStateStoreTests(unittest.TestCase):
    def test_persists_auth_blob_and_dedupe_keys_across_restarts(self):
        with TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "messaging_state.db"
            first = SqliteMessagingStateStore(path=db_path)
            first.save({"token": "abc", "device": "d1"})
            self.assertTrue(first.reserve("whatsapp:provider_message:wamid.1"))

            second = SqliteMessagingStateStore(path=db_path)
            self.assertEqual(second.load(), {"device": "d1", "token": "abc"})
            self.assertFalse(second.reserve("whatsapp:provider_message:wamid.1"))

    def test_persists_outbound_correlation_records(self):
        with TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "messaging_state.db"
            first = SqliteMessagingStateStore(path=db_path)
            first.record(
                OutboundCorrelationRecord(
                    correlation_id="corr-1",
                    channel="whatsapp",
                    provider_message_id="wamid.9",
                    provider_thread_id="thread.2",
                    recipient="+1555",
                    session_id="session-1",
                    in_reply_to=None,
                )
            )

            second = SqliteMessagingStateStore(path=db_path)
            record = second.get("corr-1")
            self.assertIsNotNone(record)
            assert record is not None
            self.assertEqual(record.provider_message_id, "wamid.9")
            self.assertEqual(record.provider_thread_id, "thread.2")
            self.assertEqual(record.recipient, "+1555")


if __name__ == "__main__":
    unittest.main()

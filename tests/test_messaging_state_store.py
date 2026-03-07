from __future__ import annotations

import sqlite3
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


    def test_expires_inbound_dedupe_keys_with_ttl_cleanup(self):
        with TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "messaging_state.db"
            store = SqliteMessagingStateStore(path=db_path, inbound_dedupe_ttl_seconds=10.0)

            with sqlite3.connect(db_path) as conn:
                conn.execute(
                    "INSERT INTO inbound_dedupe_keys(dedupe_key, created_at) VALUES(?, ?)",
                    ("whatsapp:provider_message:old", 50.0),
                )
                conn.execute(
                    "INSERT INTO inbound_dedupe_keys(dedupe_key, created_at) VALUES(?, ?)",
                    ("whatsapp:provider_message:fresh", 95.0),
                )
                conn.commit()

            deleted = store.cleanup_inbound_dedupe_keys(now=100.0)
            self.assertEqual(deleted, 1)

            with sqlite3.connect(db_path) as conn:
                keys = [row[0] for row in conn.execute("SELECT dedupe_key FROM inbound_dedupe_keys ORDER BY dedupe_key")]
            self.assertEqual(keys, ["whatsapp:provider_message:fresh"])

    def test_scoped_dedupe_keys_are_unique(self):
        with TemporaryDirectory() as tmp_dir:
            db_path = Path(tmp_dir) / "messaging_state.db"
            store = SqliteMessagingStateStore(path=db_path)

            key_session_1 = "whatsapp:session:session-1:account:acct-1:provider_message:wamid-1"
            key_session_2 = "whatsapp:session:session-2:account:acct-1:provider_message:wamid-1"

            self.assertTrue(store.reserve(key_session_1))
            self.assertFalse(store.reserve(key_session_1))
            self.assertTrue(store.reserve(key_session_2))


if __name__ == "__main__":
    unittest.main()

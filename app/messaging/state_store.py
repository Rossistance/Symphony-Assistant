"""Persistent storage interfaces and implementations for messaging runtime state."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Protocol


class WhatsAppAuthStateStore(Protocol):
    """Persistence boundary for linked-device auth/session blobs."""

    def load(self) -> dict[str, Any]:
        """Load the previously persisted auth/session blob."""

    def save(self, state: Mapping[str, Any]) -> None:
        """Persist the latest auth/session blob."""


class ProcessedInboundMessageStore(Protocol):
    """Persistence boundary for inbound message deduplication keys."""

    def reserve(self, dedupe_key: str) -> bool:
        """Reserve key for processing. Returns False when key was already seen."""


@dataclass(frozen=True)
class OutboundCorrelationRecord:
    """Persistent correlation record for outbound sends."""

    correlation_id: str
    channel: str
    provider_message_id: str
    provider_thread_id: str | None
    recipient: str
    session_id: str | None
    in_reply_to: str | None
    created_at: float = field(default_factory=time.time)


class OutboundCorrelationStore(Protocol):
    """Persistence boundary for outbound correlation lookups."""

    def record(self, entry: OutboundCorrelationRecord) -> None:
        """Persist one outbound correlation entry."""

    def get(self, correlation_id: str) -> OutboundCorrelationRecord | None:
        """Retrieve a correlation entry by correlation id."""




@dataclass
class FileWhatsAppAuthStateStore:
    """JSON-file implementation of WhatsApp auth/session blob persistence."""

    path: Path

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def save(self, state: Mapping[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(dict(state), sort_keys=True), encoding="utf-8")


@dataclass
class InMemoryWhatsAppAuthStateStore:
    """In-memory auth/session blob store for tests."""

    state: dict[str, Any] = field(default_factory=dict)

    def load(self) -> dict[str, Any]:
        return dict(self.state)

    def save(self, state: Mapping[str, Any]) -> None:
        self.state = dict(state)


@dataclass
class InMemoryProcessedInboundMessageStore:
    """In-memory dedupe store for tests."""

    _seen: set[str] = field(default_factory=set)

    def reserve(self, dedupe_key: str) -> bool:
        if dedupe_key in self._seen:
            return False
        self._seen.add(dedupe_key)
        return True


@dataclass
class InMemoryOutboundCorrelationStore:
    """In-memory outbound correlation store for tests."""

    _records: dict[str, OutboundCorrelationRecord] = field(default_factory=dict)

    def record(self, entry: OutboundCorrelationRecord) -> None:
        self._records[entry.correlation_id] = entry

    def get(self, correlation_id: str) -> OutboundCorrelationRecord | None:
        return self._records.get(correlation_id)


@dataclass
class SqliteMessagingStateStore(
    WhatsAppAuthStateStore,
    ProcessedInboundMessageStore,
    OutboundCorrelationStore,
):
    """SQLite-backed runtime store for auth blobs, inbound dedupe, and outbound correlation."""

    path: Path

    def __post_init__(self) -> None:
        self._lock = threading.RLock()
        self._initialize()

    def load(self) -> dict[str, Any]:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT payload FROM whatsapp_auth_state WHERE session_key = 'default'"
            ).fetchone()
            if row is None:
                return {}
            return json.loads(str(row[0]))

    def save(self, state: Mapping[str, Any]) -> None:
        payload = json.dumps(dict(state), sort_keys=True)
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO whatsapp_auth_state(session_key, payload, updated_at)
                VALUES('default', ?, ?)
                ON CONFLICT(session_key) DO UPDATE
                SET payload=excluded.payload, updated_at=excluded.updated_at
                """,
                (payload, time.time()),
            )
            conn.commit()

    def reserve(self, dedupe_key: str) -> bool:
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                "INSERT OR IGNORE INTO inbound_dedupe_keys(dedupe_key, created_at) VALUES(?, ?)",
                (dedupe_key, time.time()),
            )
            conn.commit()
            return cursor.rowcount > 0

    def record(self, entry: OutboundCorrelationRecord) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO outbound_correlations(
                    correlation_id,
                    channel,
                    provider_message_id,
                    provider_thread_id,
                    recipient,
                    session_id,
                    in_reply_to,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(correlation_id) DO UPDATE SET
                    channel=excluded.channel,
                    provider_message_id=excluded.provider_message_id,
                    provider_thread_id=excluded.provider_thread_id,
                    recipient=excluded.recipient,
                    session_id=excluded.session_id,
                    in_reply_to=excluded.in_reply_to,
                    created_at=excluded.created_at
                """,
                (
                    entry.correlation_id,
                    entry.channel,
                    entry.provider_message_id,
                    entry.provider_thread_id,
                    entry.recipient,
                    entry.session_id,
                    entry.in_reply_to,
                    entry.created_at,
                ),
            )
            conn.commit()

    def get(self, correlation_id: str) -> OutboundCorrelationRecord | None:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                """
                SELECT correlation_id, channel, provider_message_id, provider_thread_id,
                       recipient, session_id, in_reply_to, created_at
                FROM outbound_correlations
                WHERE correlation_id = ?
                """,
                (correlation_id,),
            ).fetchone()
            if row is None:
                return None
            return OutboundCorrelationRecord(
                correlation_id=str(row[0]),
                channel=str(row[1]),
                provider_message_id=str(row[2]),
                provider_thread_id=str(row[3]) if row[3] is not None else None,
                recipient=str(row[4]),
                session_id=str(row[5]) if row[5] is not None else None,
                in_reply_to=str(row[6]) if row[6] is not None else None,
                created_at=float(row[7]),
            )

    def _initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS whatsapp_auth_state(
                    session_key TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS inbound_dedupe_keys(
                    dedupe_key TEXT PRIMARY KEY,
                    created_at REAL NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS outbound_correlations(
                    correlation_id TEXT PRIMARY KEY,
                    channel TEXT NOT NULL,
                    provider_message_id TEXT NOT NULL,
                    provider_thread_id TEXT,
                    recipient TEXT NOT NULL,
                    session_id TEXT,
                    in_reply_to TEXT,
                    created_at REAL NOT NULL
                )
                """
            )
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)


__all__ = [
    "FileWhatsAppAuthStateStore",
    "InMemoryOutboundCorrelationStore",
    "InMemoryProcessedInboundMessageStore",
    "InMemoryWhatsAppAuthStateStore",
    "OutboundCorrelationRecord",
    "OutboundCorrelationStore",
    "ProcessedInboundMessageStore",
    "SqliteMessagingStateStore",
    "WhatsAppAuthStateStore",
]

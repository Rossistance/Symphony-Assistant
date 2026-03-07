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


class TaskRecordStore(Protocol):
    """Persistence boundary for HTTP task records."""

    def put(self, task_id: str, task: dict[str, Any]) -> None:
        """Persist or update a task record."""

    def get(self, task_id: str) -> dict[str, Any] | None:
        """Fetch a task record by id."""

    def next_task_sequence(self) -> int:
        """Return the next default task sequence number."""


class EventRecordStore(Protocol):
    """Persistence boundary for emitted HTTP event records."""

    def append(self, event_type: str, payload: dict[str, Any]) -> None:
        """Persist one event payload."""

    def list(self) -> list[dict[str, Any]]:
        """List persisted events in insertion order."""




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
class InMemoryTaskRecordStore:
    """In-memory task store for tests and local runtime."""

    _tasks: dict[str, dict[str, Any]] = field(default_factory=dict)

    def put(self, task_id: str, task: dict[str, Any]) -> None:
        self._tasks[task_id] = dict(task)

    def get(self, task_id: str) -> dict[str, Any] | None:
        record = self._tasks.get(task_id)
        return dict(record) if record is not None else None

    def next_task_sequence(self) -> int:
        max_seen = 0
        for task_id in self._tasks:
            if not task_id.startswith("task-"):
                continue
            suffix = task_id.removeprefix("task-")
            if suffix.isdigit():
                max_seen = max(max_seen, int(suffix))
        return max_seen + 1


@dataclass
class InMemoryEventRecordStore:
    """In-memory event store for tests and local runtime."""

    _events: list[dict[str, Any]] = field(default_factory=list)

    def append(self, event_type: str, payload: dict[str, Any]) -> None:
        self._events.append({"event_type": event_type, "payload": dict(payload)})

    def list(self) -> list[dict[str, Any]]:
        return [{"event_type": str(item["event_type"]), "payload": dict(item["payload"])} for item in self._events]


@dataclass
class SqliteMessagingStateStore(
    WhatsAppAuthStateStore,
    ProcessedInboundMessageStore,
    OutboundCorrelationStore,
):
    """SQLite-backed runtime store for auth blobs, inbound dedupe, and outbound correlation."""

    path: Path
    inbound_dedupe_ttl_seconds: float | None = None

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
            now = time.time()
            if self.inbound_dedupe_ttl_seconds is not None:
                self._cleanup_inbound_dedupe_keys_conn(
                    conn,
                    retention_seconds=self.inbound_dedupe_ttl_seconds,
                    now=now,
                )
            cursor = conn.execute(
                "INSERT OR IGNORE INTO inbound_dedupe_keys(dedupe_key, created_at) VALUES(?, ?)",
                (dedupe_key, now),
            )
            conn.commit()
            return cursor.rowcount > 0

    def cleanup_inbound_dedupe_keys(
        self,
        *,
        retention_seconds: float | None = None,
        now: float | None = None,
    ) -> int:
        effective_retention = self.inbound_dedupe_ttl_seconds if retention_seconds is None else retention_seconds
        if effective_retention is None:
            return 0
        current_time = time.time() if now is None else now
        with self._lock, self._connect() as conn:
            deleted = self._cleanup_inbound_dedupe_keys_conn(
                conn,
                retention_seconds=effective_retention,
                now=current_time,
            )
            conn.commit()
            return deleted

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
                CREATE INDEX IF NOT EXISTS idx_inbound_dedupe_keys_created_at
                ON inbound_dedupe_keys(created_at)
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

    def _cleanup_inbound_dedupe_keys_conn(
        self,
        conn: sqlite3.Connection,
        *,
        retention_seconds: float,
        now: float,
    ) -> int:
        cutoff = now - retention_seconds
        cursor = conn.execute("DELETE FROM inbound_dedupe_keys WHERE created_at < ?", (cutoff,))
        return int(cursor.rowcount)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)


@dataclass
class SqliteHttpSurfaceStateStore(TaskRecordStore, EventRecordStore):
    """SQLite-backed store for HTTP task/event records with migration-safe schema setup."""

    path: Path

    def __post_init__(self) -> None:
        self._lock = threading.RLock()
        self._initialize()

    def put(self, task_id: str, task: dict[str, Any]) -> None:
        payload = json.dumps(task, sort_keys=True)
        status = str(task.get("status", "unknown"))
        delegation_mode = task.get("delegation_mode")
        delegation_stages = json.dumps(task.get("delegation_stages") or [], sort_keys=True)
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                INSERT INTO http_tasks(task_id, status, delegation_mode, delegation_stages, payload, updated_at)
                VALUES(?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    status=excluded.status,
                    delegation_mode=excluded.delegation_mode,
                    delegation_stages=excluded.delegation_stages,
                    payload=excluded.payload,
                    updated_at=excluded.updated_at
                """,
                (task_id, status, delegation_mode, delegation_stages, payload, time.time()),
            )
            conn.commit()

    def get(self, task_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as conn:
            row = conn.execute("SELECT payload FROM http_tasks WHERE task_id = ?", (task_id,)).fetchone()
            if row is None:
                return None
            return json.loads(str(row[0]))

    def next_task_sequence(self) -> int:
        with self._lock, self._connect() as conn:
            row = conn.execute(
                """
                SELECT COALESCE(MAX(CAST(SUBSTR(task_id, 6) AS INTEGER)), 0)
                FROM http_tasks
                WHERE task_id GLOB 'task-[0-9]*'
                """
            ).fetchone()
            return int(row[0]) + 1 if row is not None else 1

    def append(self, event_type: str, payload: dict[str, Any]) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT INTO http_events(event_type, payload, created_at) VALUES(?, ?, ?)",
                (event_type, json.dumps(payload, sort_keys=True), time.time()),
            )
            conn.commit()

    def list(self) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT event_type, payload FROM http_events ORDER BY id ASC"
            ).fetchall()
            return [{"event_type": str(row[0]), "payload": json.loads(str(row[1]))} for row in rows]

    def _initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock, self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS http_tasks(
                    task_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    delegation_mode TEXT,
                    delegation_stages TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS http_events(
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at REAL NOT NULL
                )
                """
            )
            self._ensure_column(conn, "http_tasks", "status", "TEXT NOT NULL DEFAULT 'unknown'")
            self._ensure_column(conn, "http_tasks", "delegation_mode", "TEXT")
            self._ensure_column(conn, "http_tasks", "delegation_stages", "TEXT NOT NULL DEFAULT '[]'")
            self._ensure_column(conn, "http_tasks", "payload", "TEXT NOT NULL DEFAULT '{}'")
            self._ensure_column(conn, "http_tasks", "updated_at", "REAL NOT NULL DEFAULT 0")
            self._ensure_column(conn, "http_events", "created_at", "REAL NOT NULL DEFAULT 0")
            conn.commit()

    def _ensure_column(self, conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        existing = {str(row[1]) for row in rows}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)


__all__ = [
    "FileWhatsAppAuthStateStore",
    "InMemoryOutboundCorrelationStore",
    "InMemoryEventRecordStore",
    "InMemoryProcessedInboundMessageStore",
    "InMemoryTaskRecordStore",
    "InMemoryWhatsAppAuthStateStore",
    "EventRecordStore",
    "OutboundCorrelationRecord",
    "OutboundCorrelationStore",
    "ProcessedInboundMessageStore",
    "SqliteHttpSurfaceStateStore",
    "SqliteMessagingStateStore",
    "TaskRecordStore",
    "WhatsAppAuthStateStore",
]

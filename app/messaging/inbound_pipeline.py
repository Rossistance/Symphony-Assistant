"""Inbound message ingestion entrypoint with provider-ID deduplication."""

from __future__ import annotations

import json
from dataclasses import dataclass
import time
from pathlib import Path
from typing import Callable, Protocol, cast

from app.messaging.base import InboundMessage
from app.messaging.state_store import InMemoryProcessedInboundMessageStore, ProcessedInboundMessageStore


InboundDedupeStore = ProcessedInboundMessageStore
InMemoryInboundDedupeStore = InMemoryProcessedInboundMessageStore


class InboundDedupeCleanupStore(Protocol):
    """Optional dedupe store capability for retention cleanup."""

    def cleanup_inbound_dedupe_keys(self, *, retention_seconds: float | None = None) -> int:
        """Delete expired inbound dedupe keys and return number removed."""

@dataclass
class FileInboundDedupeStore:
    """JSON-file dedupe store to persist inbound dedupe keys across restarts."""

    path: Path

    def reserve(self, dedupe_key: str) -> bool:
        seen = self._load()
        if dedupe_key in seen:
            return False
        seen.add(dedupe_key)
        self._save(seen)
        return True

    def _load(self) -> set[str]:
        if not self.path.exists():
            return set()
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if not isinstance(payload, list):
            return set()
        return {str(item) for item in payload}

    def _save(self, seen: set[str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(sorted(seen)), encoding="utf-8")


@dataclass(frozen=True)
class InboundIngestionResult:
    """Outcome for one inbound event ingestion."""

    accepted: bool
    dedupe_key: str


class InboundIngestionPipeline:
    """Shared ingestion pipeline for webhook and gateway inbound events."""

    def __init__(
        self,
        *,
        dedupe_store: InboundDedupeStore,
        event_emitter: Callable[[str, dict[str, str]], None] | None = None,
        dedupe_retention_seconds: float | None = None,
        dedupe_cleanup_interval_seconds: float | None = None,
    ) -> None:
        self._dedupe_store = dedupe_store
        self._event_emitter = event_emitter
        self._dedupe_retention_seconds = dedupe_retention_seconds
        self._dedupe_cleanup_interval_seconds = dedupe_cleanup_interval_seconds
        self._last_cleanup_at = 0.0
        self._cleanup_inbound_dedupe_keys(force=True)

    def ingest(self, inbound: InboundMessage, *, source: str) -> InboundIngestionResult:
        self._cleanup_inbound_dedupe_keys()
        dedupe_key = self._build_dedupe_key(inbound)
        accepted = self._dedupe_store.reserve(dedupe_key)
        event_type = "message.inbound.accepted" if accepted else "message.inbound.duplicate"
        self._emit(
            event_type,
            {
                "source": source,
                "channel": inbound.channel,
                "from": inbound.from_user,
                "provider_message_id": inbound.provider_message_id or "",
                "provider_thread_id": inbound.provider_thread_id or "",
                "dedupe_key": dedupe_key,
            },
        )
        return InboundIngestionResult(accepted=accepted, dedupe_key=dedupe_key)

    def _emit(self, event_type: str, payload: dict[str, str]) -> None:
        if self._event_emitter is None:
            return
        self._event_emitter(event_type, payload)

    def _cleanup_inbound_dedupe_keys(self, *, force: bool = False) -> None:
        store = self._dedupe_store
        if not hasattr(store, "cleanup_inbound_dedupe_keys"):
            return

        if (
            not force
            and self._dedupe_cleanup_interval_seconds is not None
            and self._dedupe_cleanup_interval_seconds >= 0
        ):
            now = time.time()
            elapsed = now - self._last_cleanup_at
            if elapsed < self._dedupe_cleanup_interval_seconds:
                return
        cleaner = cast(InboundDedupeCleanupStore, store)
        cleaner.cleanup_inbound_dedupe_keys(retention_seconds=self._dedupe_retention_seconds)
        self._last_cleanup_at = time.time()

    @staticmethod
    def _build_scope_prefix(inbound: InboundMessage) -> str:
        scope_parts: list[str] = []
        session_id = str(inbound.metadata.get("session_id") or "").strip()
        if session_id:
            scope_parts.append(f"session:{session_id}")

        account_id = str(inbound.metadata.get("account_id") or "").strip()
        if account_id:
            scope_parts.append(f"account:{account_id}")

        if not scope_parts:
            return ""
        return ":".join(scope_parts) + ":"

    @staticmethod
    def _build_dedupe_key(inbound: InboundMessage) -> str:
        scope_prefix = InboundIngestionPipeline._build_scope_prefix(inbound)
        provider_id = (inbound.provider_message_id or "").strip()
        if provider_id:
            return f"{inbound.channel}:{scope_prefix}provider_message:{provider_id}"

        provider_thread_id = (inbound.provider_thread_id or "").strip()
        if provider_thread_id:
            return f"{inbound.channel}:{scope_prefix}provider_thread:{provider_thread_id}"

        stable_provider_id = str(inbound.metadata.get("stable_provider_id") or "").strip()
        if stable_provider_id:
            return f"{inbound.channel}:{scope_prefix}metadata_provider:{stable_provider_id}"
        raise ValueError("InboundMessage must include a provider identifier for deduplication")


__all__ = [
    "InboundDedupeStore",
    "InboundIngestionPipeline",
    "InboundIngestionResult",
    "FileInboundDedupeStore",
    "InMemoryInboundDedupeStore",
]

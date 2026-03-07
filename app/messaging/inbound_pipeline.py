"""Inbound message ingestion entrypoint with provider-ID deduplication."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Protocol

from app.messaging.base import InboundMessage


class InboundDedupeStore(Protocol):
    """Persistence boundary for inbound deduplication state."""

    def reserve(self, dedupe_key: str) -> bool:
        """Reserve key for processing. Returns False when the key already exists."""


@dataclass
class InMemoryInboundDedupeStore:
    """In-memory dedupe store for tests/local usage."""

    _seen: set[str] = field(default_factory=set)

    def reserve(self, dedupe_key: str) -> bool:
        if dedupe_key in self._seen:
            return False
        self._seen.add(dedupe_key)
        return True


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
    ) -> None:
        self._dedupe_store = dedupe_store
        self._event_emitter = event_emitter

    def ingest(self, inbound: InboundMessage, *, source: str) -> InboundIngestionResult:
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

    @staticmethod
    def _build_dedupe_key(inbound: InboundMessage) -> str:
        provider_id = (inbound.provider_message_id or "").strip()
        if provider_id:
            return f"{inbound.channel}:provider_message:{provider_id}"

        provider_thread_id = (inbound.provider_thread_id or "").strip()
        if provider_thread_id:
            return f"{inbound.channel}:provider_thread:{provider_thread_id}"

        stable_provider_id = str(inbound.metadata.get("stable_provider_id") or "").strip()
        if stable_provider_id:
            return f"{inbound.channel}:metadata_provider:{stable_provider_id}"

        return f"{inbound.channel}:sender:{inbound.from_user.strip()}:body:{inbound.body.strip()}"


__all__ = [
    "InboundDedupeStore",
    "InboundIngestionPipeline",
    "InboundIngestionResult",
    "InMemoryInboundDedupeStore",
]

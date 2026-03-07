"""Inbound message ingestion entrypoint with provider-ID deduplication."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from app.messaging.base import InboundMessage
from app.messaging.state_store import InMemoryProcessedInboundMessageStore, ProcessedInboundMessageStore


InboundDedupeStore = ProcessedInboundMessageStore
InMemoryInboundDedupeStore = InMemoryProcessedInboundMessageStore


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
        raise ValueError("InboundMessage must include a provider identifier for deduplication")


__all__ = [
    "InboundDedupeStore",
    "InboundIngestionPipeline",
    "InboundIngestionResult",
    "FileInboundDedupeStore",
    "InMemoryInboundDedupeStore",
]

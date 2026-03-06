"""Shared transport abstractions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class InboundMessage:
    """Normalized inbound message context across providers."""

    channel: str
    from_user: str
    body: str
    provider_message_id: str | None = None
    provider_thread_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class OutboundResult:
    """Result from transport provider APIs."""

    provider_message_id: str
    provider_thread_id: str | None = None
    raw_response: dict[str, Any] | None = None


class MessagingTransport(Protocol):
    """Transport contract used by orchestration/runtime code."""

    channel: str

    def send_message(self, to: str, text: str, *, thread_id: str | None = None) -> OutboundResult:
        """Send a plain-text message."""

    def send_media(
        self,
        to: str,
        media_url: str,
        *,
        caption: str | None = None,
        thread_id: str | None = None,
    ) -> OutboundResult:
        """Send media with optional caption."""

    def reply_in_thread(self, inbound: InboundMessage, text: str) -> OutboundResult:
        """Reply in provider thread when supported."""

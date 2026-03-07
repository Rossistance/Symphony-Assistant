"""WhatsApp transport adapter backed by a gateway-managed session."""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import uuid4
from typing import Any

from app.messaging.base import InboundMessage, OutboundResult
from app.messaging.gateway_client import GatewayClient, HttpGatewayClient
from app.messaging.state_store import InMemoryOutboundCorrelationStore, OutboundCorrelationRecord, OutboundCorrelationStore


def _first_non_empty(response: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = response.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


@dataclass
class WhatsAppCloudAdapter:
    """MessagingTransport-compatible adapter for WhatsApp gateway calls."""

    channel = "whatsapp"
    session_id: str | None = None
    gateway_client: GatewayClient = field(default_factory=HttpGatewayClient.from_env)
    correlation_store: OutboundCorrelationStore = field(default_factory=InMemoryOutboundCorrelationStore)

    def __post_init__(self) -> None:
        if self.session_id is None:
            self.session_id = self.gateway_client.session_id

    def _send_via_gateway(self, payload: dict[str, Any]) -> OutboundResult:
        if not self.session_id:
            raise RuntimeError("WHATSAPP_GATEWAY_SESSION_ID is required for WhatsApp gateway transport")

        response = self.gateway_client.send(session_id=self.session_id, payload=payload)
        provider_message_id = _first_non_empty(response, "provider_message_id", "message_id", "id")
        if provider_message_id is None:
            raise ValueError("Gateway response is missing provider message id")

        provider_thread_id = _first_non_empty(
            response,
            "provider_thread_id",
            "thread_id",
            "conversation_id",
        )
        correlation_id = str(payload.get("correlation_id") or uuid4())
        self.correlation_store.record(
            OutboundCorrelationRecord(
                correlation_id=correlation_id,
                channel=self.channel,
                provider_message_id=provider_message_id,
                provider_thread_id=provider_thread_id,
                recipient=str(payload.get("to") or ""),
                session_id=self.session_id,
                in_reply_to=str(payload.get("in_reply_to")) if payload.get("in_reply_to") else None,
            )
        )

        raw_response = {**response, "correlation_id": correlation_id}
        return OutboundResult(
            provider_message_id=provider_message_id,
            provider_thread_id=provider_thread_id,
            raw_response=raw_response,
        )

    def send_message(self, to: str, text: str, *, thread_id: str | None = None) -> OutboundResult:
        payload = {
            "to": to,
            "type": "text",
            "text": text,
            "thread_id": thread_id,
        }
        return self._send_via_gateway(payload)

    def send_media(
        self,
        to: str,
        media_url: str,
        *,
        caption: str | None = None,
        thread_id: str | None = None,
    ) -> OutboundResult:
        payload = {
            "to": to,
            "type": "media",
            "media_url": media_url,
            "caption": caption,
            "thread_id": thread_id,
        }
        return self._send_via_gateway(payload)

    def reply_in_thread(self, inbound: InboundMessage, text: str) -> OutboundResult:
        payload = {
            "to": inbound.from_user,
            "type": "text",
            "text": text,
            "thread_id": inbound.provider_thread_id or inbound.provider_message_id,
            "in_reply_to": inbound.provider_message_id,
        }
        return self._send_via_gateway(payload)

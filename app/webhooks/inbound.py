"""Normalize inbound webhook payloads while preserving provider IDs."""

from __future__ import annotations

from typing import Any

from app.messaging.base import InboundMessage



def parse_inbound_webhook(payload: dict[str, Any], *, channel: str) -> InboundMessage:
    """Parse webhook payload from a provider into normalized message shape.

    Preserves provider-specific message/thread identifiers so downstream
    reply logic can keep responses in the originating thread.
    """

    # Common aliases across Twilio/WhatsApp/iOS bridge payloads.
    provider_message_id = payload.get("message_id") or payload.get("MessageSid") or payload.get("id")
    provider_thread_id = (
        payload.get("thread_id")
        or payload.get("context_id")
        or payload.get("conversation_id")
        or payload.get("WaId")
    )
    from_user = payload.get("from") or payload.get("From") or payload.get("sender")
    body = payload.get("body") or payload.get("Body") or payload.get("text") or ""

    return InboundMessage(
        channel=channel,
        from_user=from_user,
        body=body,
        provider_message_id=provider_message_id,
        provider_thread_id=provider_thread_id,
        metadata=payload,
    )

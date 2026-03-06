"""WhatsApp Cloud API adapter."""

from __future__ import annotations

import uuid

from app.messaging.base import InboundMessage, OutboundResult


class WhatsAppCloudAdapter:
    channel = "whatsapp"

    def send_message(self, to: str, text: str, *, thread_id: str | None = None) -> OutboundResult:
        message_id = f"wa-{uuid.uuid4()}"
        response = {"to": to, "text": text, "context": {"thread_id": thread_id}}
        return OutboundResult(provider_message_id=message_id, provider_thread_id=thread_id, raw_response=response)

    def send_media(
        self,
        to: str,
        media_url: str,
        *,
        caption: str | None = None,
        thread_id: str | None = None,
    ) -> OutboundResult:
        message_id = f"wa-{uuid.uuid4()}"
        response = {
            "to": to,
            "media_url": media_url,
            "caption": caption,
            "context": {"thread_id": thread_id},
        }
        return OutboundResult(provider_message_id=message_id, provider_thread_id=thread_id, raw_response=response)

    def reply_in_thread(self, inbound: InboundMessage, text: str) -> OutboundResult:
        return self.send_message(
            inbound.from_user,
            text,
            thread_id=inbound.provider_thread_id or inbound.provider_message_id,
        )

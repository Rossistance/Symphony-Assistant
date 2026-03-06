"""Optional iOS bridge adapter behind feature flag."""

from __future__ import annotations

import uuid

from app.messaging.base import InboundMessage, OutboundResult


class IOSBridgeAdapter:
    channel = "ios"

    def send_message(self, to: str, text: str, *, thread_id: str | None = None) -> OutboundResult:
        message_id = f"ios-{uuid.uuid4()}"
        response = {"to": to, "text": text, "thread_id": thread_id}
        return OutboundResult(provider_message_id=message_id, provider_thread_id=thread_id, raw_response=response)

    def send_media(
        self,
        to: str,
        media_url: str,
        *,
        caption: str | None = None,
        thread_id: str | None = None,
    ) -> OutboundResult:
        message_id = f"ios-{uuid.uuid4()}"
        response = {"to": to, "media_url": media_url, "caption": caption, "thread_id": thread_id}
        return OutboundResult(provider_message_id=message_id, provider_thread_id=thread_id, raw_response=response)

    def reply_in_thread(self, inbound: InboundMessage, text: str) -> OutboundResult:
        return self.send_message(inbound.from_user, text, thread_id=inbound.provider_thread_id)

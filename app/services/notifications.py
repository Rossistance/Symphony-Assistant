"""Outbound messaging entrypoint used by orchestration/actions."""

from __future__ import annotations

from app.messaging.base import InboundMessage, OutboundResult
from app.messaging.router import MessagingRouter

router = MessagingRouter()


# Refactored from SMS-direct calls to adapter abstraction.
def send_user_message(to: str, text: str, *, channel: str | None = None, thread_id: str | None = None) -> OutboundResult:
    return router.send_message(to, text, channel=channel, thread_id=thread_id)


def send_user_media(
    to: str,
    media_url: str,
    *,
    caption: str | None = None,
    channel: str | None = None,
    thread_id: str | None = None,
) -> OutboundResult:
    return router.send_media(
        to,
        media_url,
        channel=channel,
        caption=caption,
        thread_id=thread_id,
    )


def reply_to_inbound(inbound: InboundMessage, text: str) -> OutboundResult:
    return router.reply_in_thread(inbound, text)

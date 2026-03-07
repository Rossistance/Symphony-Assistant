"""Channel-aware transport routing with fallback policy."""

from __future__ import annotations

from dataclasses import dataclass, field

from app.config import MessagingConfig, config
from app.messaging.adapters.ios_bridge import IOSBridgeAdapter
from app.messaging.adapters.sms import SmsAdapter
from app.messaging.adapters.whatsapp import WhatsAppCloudAdapter
from app.messaging.base import InboundMessage, MessagingTransport, OutboundResult
from app.messaging.runtime_state import get_runtime_state_store


@dataclass
class MessagingRouter:
    """Selects a messaging adapter using channel + fallback rules."""

    settings: MessagingConfig = config
    adapters: dict[str, MessagingTransport] = field(default_factory=dict)

    def __post_init__(self) -> None:
        default_adapters: dict[str, MessagingTransport] = {
            "whatsapp": WhatsAppCloudAdapter(correlation_store=get_runtime_state_store()),
            "sms": SmsAdapter(),
        }
        if self.settings.enable_ios_bridge:
            default_adapters["ios"] = IOSBridgeAdapter()

        self._adapters: dict[str, MessagingTransport] = {**default_adapters, **self.adapters}

    def _get_adapter(self, channel: str | None) -> MessagingTransport:
        preferred = channel or self.settings.default_channel
        adapter = self._adapters.get(preferred)
        if adapter:
            return adapter

        if self.settings.enable_sms_fallback:
            return self._adapters["sms"]

        raise ValueError(f"No adapter configured for channel '{preferred}' and SMS fallback disabled")

    def send_message(self, to: str, text: str, *, channel: str | None = None, thread_id: str | None = None) -> OutboundResult:
        return self._get_adapter(channel).send_message(to, text, thread_id=thread_id)

    def send_media(
        self,
        to: str,
        media_url: str,
        *,
        channel: str | None = None,
        caption: str | None = None,
        thread_id: str | None = None,
    ) -> OutboundResult:
        return self._get_adapter(channel).send_media(to, media_url, caption=caption, thread_id=thread_id)

    def reply_in_thread(self, inbound: InboundMessage, text: str) -> OutboundResult:
        return self._get_adapter(inbound.channel).reply_in_thread(inbound, text)

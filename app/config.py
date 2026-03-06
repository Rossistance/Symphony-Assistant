"""Runtime configuration for transport and channel routing."""

from __future__ import annotations

import os
from dataclasses import dataclass



def _as_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class MessagingConfig:
    """Feature flags and defaults for messaging adapters."""

    default_channel: str = os.getenv("DEFAULT_CHANNEL", "whatsapp")
    enable_sms_fallback: bool = _as_bool("ENABLE_SMS_FALLBACK", True)
    enable_ios_bridge: bool = _as_bool("ENABLE_IOS_BRIDGE", False)


config = MessagingConfig()

"""Runtime configuration for transport, channels, and model routing."""

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


@dataclass(frozen=True)
class ModelRouterConfig:
    """Configuration for provider discovery and routing precedence."""

    router_order: tuple[str, ...] = tuple(
        provider.strip().lower()
        for provider in os.getenv("MODEL_ROUTER_ORDER", "groq,openai,gemini,anthropic").split(",")
        if provider.strip()
    )
    max_retries: int = int(os.getenv("MODEL_PROVIDER_MAX_RETRIES", "2"))


config = MessagingConfig()
model_config = ModelRouterConfig()

"""Provider discovery and capability matrix generation."""

from __future__ import annotations

from dataclasses import dataclass

from app.config import ModelRouterConfig, model_config
from app.models.base import ModelProvider
from app.models.providers.static_adapters import build_provider_adapters


@dataclass(frozen=True)
class ProviderRegistry:
    """Discovered providers available for runtime routing."""

    providers: dict[str, ModelProvider]
    capability_matrix: dict[str, dict[str, bool]]


def discover_providers(settings: ModelRouterConfig = model_config) -> ProviderRegistry:
    """Discover configured providers using env-backed health checks."""

    adapters = build_provider_adapters()
    ordered = [name for name in settings.router_order if name in adapters]

    active: dict[str, ModelProvider] = {}
    matrix: dict[str, dict[str, bool]] = {}
    for provider_name in ordered:
        adapter = adapters[provider_name]
        if not adapter.healthcheck():
            continue

        active[provider_name] = adapter
        matrix[provider_name] = {
            "chat": adapter.capabilities.chat,
            "tool_calling": adapter.capabilities.tool_calling,
            "embeddings": adapter.capabilities.embeddings,
        }

    return ProviderRegistry(providers=active, capability_matrix=matrix)

"""Model provider router with precedence, health checks, retries, and failover."""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import Callable

from app.config import ModelRouterConfig, model_config
from app.models.base import GenerateRequest, ProviderError, TraceMetadata, TransientProviderError
from app.models.discovery import ProviderRegistry, discover_providers


@dataclass
class ModelRouter:
    """Routes model operations to providers based on capability and health."""

    settings: ModelRouterConfig = model_config
    registry: ProviderRegistry | None = None
    sleeper: Callable[[float], None] = time.sleep

    def __post_init__(self) -> None:
        if self.registry is None:
            self.registry = discover_providers(self.settings)

    def _candidates(self, capability: str) -> list[str]:
        assert self.registry is not None
        return [
            name
            for name in self.settings.router_order
            if name in self.registry.providers and self.registry.capability_matrix[name].get(capability, False)
        ]

    def _with_failover(self, capability: str, operation: Callable[[str], object]) -> object:
        assert self.registry is not None
        candidates = self._candidates(capability)
        if not candidates:
            raise ProviderError(f"No providers available for capability '{capability}'")

        errors: list[str] = []
        for provider_name in candidates:
            provider = self.registry.providers[provider_name]
            if not provider.healthcheck():
                errors.append(f"{provider_name}: healthcheck failed")
                continue

            for attempt in range(self.settings.max_retries + 1):
                try:
                    return operation(provider_name)
                except TransientProviderError as exc:
                    errors.append(f"{provider_name}[attempt={attempt + 1}]: {exc}")
                    if attempt >= self.settings.max_retries:
                        break
                    self.sleeper(0.05 + random.random() * 0.05)
                except ProviderError as exc:
                    errors.append(f"{provider_name}: {exc}")
                    break

        raise ProviderError("All providers failed. " + " | ".join(errors))

    def generate(self, request: GenerateRequest, trace: TraceMetadata) -> dict[str, object]:
        assert self.registry is not None

        def invoke(provider_name: str) -> dict[str, object]:
            provider = self.registry.providers[provider_name]
            return provider.generate(request, trace)

        return self._with_failover("chat", invoke)  # type: ignore[return-value]

    def embed(self, texts: list[str], trace: TraceMetadata) -> list[list[float]]:
        assert self.registry is not None

        def invoke(provider_name: str) -> list[list[float]]:
            provider = self.registry.providers[provider_name]
            return provider.embed(texts, trace)

        return self._with_failover("embeddings", invoke)  # type: ignore[return-value]

    def healthcheck(self) -> bool:
        return bool(self.registry and self.registry.providers)

"""Deprecated compatibility layer for legacy model routing interfaces.

Prefer using ``app.models.router.ModelRouter`` directly for new code.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass
from typing import Any

from app.config import ModelRouterConfig
from app.models.base import GenerateRequest, TraceMetadata, TransientProviderError
from app.models.router import ModelCallTelemetry, ModelRouter, RoutingEvent


@dataclass(frozen=True)
class ModelResponse:
    """Legacy normalized response payload from a model provider."""

    output_text: str
    tokens_in: int
    tokens_out: int
    estimated_cost: float


class RoutedModelClient:
    """Deprecated wrapper that proxies legacy calls to ``ModelRouter``."""

    def __init__(
        self,
        providers: dict[str, Any],
        fallback_map: dict[str, str] | None = None,
        max_attempts: int = 3,
        base_backoff_seconds: float = 0.05,
        jitter_ratio: float = 0.2,
        sleep_fn: Any | None = None,
        random_fn: Any | None = None,
    ) -> None:
        warnings.warn(
            "RoutedModelClient is deprecated; use app.models.router.ModelRouter instead.",
            DeprecationWarning,
            stacklevel=2,
        )
        del fallback_map, base_backoff_seconds, jitter_ratio

        provider_registry = type(
            "ProviderRegistryCompat",
            (),
            {
                "providers": providers,
                "capability_matrix": {name: {"chat": True, "embeddings": False} for name in providers},
            },
        )()
        self.router = ModelRouter(
            settings=ModelRouterConfig(router_order=tuple(providers), max_retries=max(0, max_attempts - 1)),
            registry=provider_registry,
            sleeper=sleep_fn or (lambda _: None),
            random_fn=random_fn or (lambda: 0),
        )

    @property
    def events(self) -> list[RoutingEvent]:
        return self.router.events

    @property
    def telemetry_log(self) -> list[ModelCallTelemetry]:
        return self.router.telemetry_log

    def generate(self, *, model: str, prompt: str, provider: str) -> ModelResponse:
        message = {"role": "user", "content": prompt}
        request = GenerateRequest(messages=[message])
        trace = TraceMetadata(
            run_id=f"legacy-{model}",
            agent_id="legacy-routed-model-client",
            task_type="chat",
            budget_context={"provider_hint": provider},
        )
        result = self.router.generate(request, trace)
        return ModelResponse(
            output_text=str(result.get("content", result.get("output_text", ""))),
            tokens_in=int(result.get("tokens_in", 0)),
            tokens_out=int(result.get("tokens_out", 0)),
            estimated_cost=float(result.get("estimated_cost", 0)),
        )


__all__ = ["ModelResponse", "RoutedModelClient", "TransientProviderError"]

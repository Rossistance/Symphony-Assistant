"""Model provider router with precedence, health checks, retries, and failover."""

from __future__ import annotations

import random
import time
from dataclasses import dataclass
from typing import Any, Callable

from app.config import ModelRouterConfig, model_config
from app.models.base import GenerateRequest, ProviderError, TraceMetadata, TransientProviderError
from app.models.discovery import ProviderRegistry, discover_providers


@dataclass(frozen=True)
class RoutingEvent:
    """Event emitted during route selection and fallback transitions."""

    event_type: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class FallbackTransition:
    """Transition metadata from one provider to another."""

    from_provider: str
    to_provider: str
    reason: str


@dataclass(frozen=True)
class ModelCallTelemetry:
    """Per-call telemetry used for observability and replay."""

    capability: str
    provider_selected: str
    run_id: str
    task_type: str
    latency_ms: float
    final_status: str
    fallback_transitions: list[FallbackTransition]
    error_summary: str | None = None


@dataclass
class ModelRouter:
    """Routes model operations to providers based on capability and health."""

    settings: ModelRouterConfig = model_config
    registry: ProviderRegistry | None = None
    sleeper: Callable[[float], None] = time.sleep
    random_fn: Callable[[], float] = random.random
    clock: Callable[[], float] = time.perf_counter

    def __post_init__(self) -> None:
        if self.registry is None:
            self.registry = discover_providers(self.settings)
        self.events: list[RoutingEvent] = []
        self.telemetry_log: list[ModelCallTelemetry] = []

    def _emit_event(self, event_type: str, payload: dict[str, Any]) -> None:
        self.events.append(RoutingEvent(event_type=event_type, payload=payload))

    def _candidates(self, capability: str) -> list[str]:
        assert self.registry is not None
        return [
            name
            for name in self.settings.router_order
            if name in self.registry.providers and self.registry.capability_matrix[name].get(capability, False)
        ]

    def _with_failover(self, capability: str, trace: TraceMetadata, operation: Callable[[str], object]) -> object:
        assert self.registry is not None
        started = self.clock()
        candidates = self._candidates(capability)
        if not candidates:
            raise ProviderError(f"No providers available for capability '{capability}'")

        errors: list[str] = []
        transitions: list[FallbackTransition] = []
        last_provider = candidates[-1]

        for index, provider_name in enumerate(candidates):
            last_provider = provider_name
            provider = self.registry.providers[provider_name]
            self._emit_event(
                "provider.route.selected",
                {"provider": provider_name, "capability": capability, "attempts": self.settings.max_retries + 1},
            )

            if not provider.healthcheck():
                errors.append(f"{provider_name}: healthcheck failed")
            else:
                for attempt in range(self.settings.max_retries + 1):
                    try:
                        result = operation(provider_name)
                        self.telemetry_log.append(
                            ModelCallTelemetry(
                                capability=capability,
                                provider_selected=provider_name,
                                run_id=trace.run_id,
                                task_type=trace.task_type,
                                latency_ms=(self.clock() - started) * 1000,
                                final_status="success",
                                fallback_transitions=list(transitions),
                            )
                        )
                        return result
                    except TransientProviderError as exc:
                        errors.append(f"{provider_name}[attempt={attempt + 1}]: {exc}")
                        if attempt >= self.settings.max_retries:
                            break
                        self.sleeper(0.05 + self.random_fn() * 0.05)
                    except ProviderError as exc:
                        errors.append(f"{provider_name}: {exc}")
                        break

            next_provider_index = index + 1
            if next_provider_index < len(candidates):
                transition = FallbackTransition(
                    from_provider=provider_name,
                    to_provider=candidates[next_provider_index],
                    reason="retries_exhausted",
                )
                transitions.append(transition)
                self._emit_event(
                    "provider.route.fallback",
                    {
                        "from_provider": transition.from_provider,
                        "to_provider": transition.to_provider,
                        "capability": capability,
                        "reason": transition.reason,
                    },
                )

        error_summary = " | ".join(errors)
        self.telemetry_log.append(
            ModelCallTelemetry(
                capability=capability,
                provider_selected=last_provider,
                run_id=trace.run_id,
                task_type=trace.task_type,
                latency_ms=(self.clock() - started) * 1000,
                final_status="failed",
                fallback_transitions=transitions,
                error_summary=error_summary,
            )
        )
        raise ProviderError("All providers failed. " + error_summary)

    def generate(self, request: GenerateRequest, trace: TraceMetadata) -> dict[str, object]:
        assert self.registry is not None

        def invoke(provider_name: str) -> dict[str, object]:
            provider = self.registry.providers[provider_name]
            return provider.generate(request, trace)

        return self._with_failover("chat", trace, invoke)  # type: ignore[return-value]

    def embed(self, texts: list[str], trace: TraceMetadata) -> list[list[float]]:
        assert self.registry is not None

        def invoke(provider_name: str) -> list[list[float]]:
            provider = self.registry.providers[provider_name]
            return provider.embed(texts, trace)

        return self._with_failover("embeddings", trace, invoke)  # type: ignore[return-value]

    def healthcheck(self) -> bool:
        return bool(self.registry and self.registry.providers)

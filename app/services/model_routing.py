"""Model provider routing with retry/backoff, fallback, and telemetry."""

from __future__ import annotations

import random
import time
from dataclasses import dataclass, field
from typing import Any, Protocol


class TransientProviderError(RuntimeError):
    """Raised when a provider call fails transiently and can be retried."""


@dataclass(frozen=True)
class ModelResponse:
    """Normalized response payload from a model provider."""

    output_text: str
    tokens_in: int
    tokens_out: int
    estimated_cost: float


class ModelProvider(Protocol):
    """Provider contract for routed model requests."""

    name: str

    def generate(self, *, model: str, prompt: str) -> ModelResponse:
        """Run a completion request on the provider."""


@dataclass
class RoutingEvent:
    """Event emitted during route selection and fallback transitions."""

    event_type: str
    payload: dict[str, Any]


@dataclass
class FallbackTransition:
    """Transition metadata from one provider to another."""

    from_provider: str
    to_provider: str
    reason: str


@dataclass
class ModelCallTelemetry:
    """Persistent per-call telemetry used for observability/replay."""

    provider_selected: str
    model: str
    tokens_in: int
    tokens_out: int
    latency_ms: float
    estimated_cost: float
    final_status: str
    fallback_transitions: list[FallbackTransition] = field(default_factory=list)


@dataclass
class RoutedModelClient:
    """Routes model requests to providers with retry and fallback behavior."""

    providers: dict[str, ModelProvider]
    fallback_map: dict[str, str] = field(default_factory=dict)
    max_attempts: int = 3
    base_backoff_seconds: float = 0.05
    jitter_ratio: float = 0.2
    sleep_fn: Any = time.sleep
    random_fn: Any = random.random

    def __post_init__(self) -> None:
        self.events: list[RoutingEvent] = []
        self.telemetry_log: list[ModelCallTelemetry] = []

    def generate(self, *, model: str, prompt: str, provider: str) -> ModelResponse:
        """Execute a routed request and persist telemetry for the call."""

        request_started = time.perf_counter()
        current_provider = provider
        transitions: list[FallbackTransition] = []

        while True:
            response = self._attempt_with_retries(model=model, prompt=prompt, provider=current_provider)
            if isinstance(response, ModelResponse):
                latency_ms = (time.perf_counter() - request_started) * 1000
                telemetry = ModelCallTelemetry(
                    provider_selected=current_provider,
                    model=model,
                    tokens_in=response.tokens_in,
                    tokens_out=response.tokens_out,
                    latency_ms=latency_ms,
                    estimated_cost=response.estimated_cost,
                    final_status="success",
                    fallback_transitions=transitions,
                )
                self.telemetry_log.append(telemetry)
                return response

            next_provider = self.fallback_map.get(current_provider)
            if not next_provider:
                break

            transition = FallbackTransition(
                from_provider=current_provider,
                to_provider=next_provider,
                reason="retries_exhausted",
            )
            transitions.append(transition)
            self._emit_event(
                "provider.route.fallback",
                {
                    "from_provider": current_provider,
                    "to_provider": next_provider,
                    "model": model,
                    "reason": "retries_exhausted",
                },
            )
            current_provider = next_provider

        latency_ms = (time.perf_counter() - request_started) * 1000
        failed_telemetry = ModelCallTelemetry(
            provider_selected=current_provider,
            model=model,
            tokens_in=0,
            tokens_out=0,
            latency_ms=latency_ms,
            estimated_cost=0,
            final_status="failed",
            fallback_transitions=transitions,
        )
        self.telemetry_log.append(failed_telemetry)
        raise TransientProviderError("All provider attempts exhausted")

    def _attempt_with_retries(self, *, model: str, prompt: str, provider: str) -> ModelResponse | None:
        adapter = self.providers[provider]
        self._emit_event(
            "provider.route.selected",
            {"provider": provider, "model": model, "attempts": self.max_attempts},
        )
        for attempt in range(1, self.max_attempts + 1):
            try:
                return adapter.generate(model=model, prompt=prompt)
            except TransientProviderError:
                if attempt >= self.max_attempts:
                    return None
                delay = self.base_backoff_seconds * (2 ** (attempt - 1))
                jitter = delay * self.jitter_ratio * self.random_fn()
                self.sleep_fn(delay + jitter)

        return None

    def _emit_event(self, event_type: str, payload: dict[str, Any]) -> None:
        self.events.append(RoutingEvent(event_type=event_type, payload=payload))

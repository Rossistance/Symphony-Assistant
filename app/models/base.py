"""Provider interface contracts and shared routing data structures."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class ProviderCapabilities:
    """Capabilities exposed by a model provider adapter."""

    chat: bool = True
    tool_calling: bool = True
    embeddings: bool = False


@dataclass(frozen=True)
class TraceMetadata:
    """Tracing context propagated through all routed model calls."""

    run_id: str
    agent_id: str
    task_type: str
    budget_context: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GenerateRequest:
    """Normalized text generation request."""

    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]] | None = None
    response_schema: dict[str, Any] | None = None
    temperature: float = 0.2
    max_tokens: int = 1024


class ProviderError(Exception):
    """Base provider error."""


class TransientProviderError(ProviderError):
    """Signals failures that can be retried and/or failed over."""


class ModelProvider(Protocol):
    """Normalized provider interface implemented by every adapter."""

    name: str
    capabilities: ProviderCapabilities

    def generate(self, request: GenerateRequest, trace: TraceMetadata) -> dict[str, Any]:
        """Generate a model response for chat/tool tasks."""

    def embed(self, texts: list[str], trace: TraceMetadata) -> list[list[float]]:
        """Create embeddings for input text list."""

    def healthcheck(self) -> bool:
        """Check provider health/readiness."""

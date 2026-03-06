"""Lightweight provider adapters with normalized interfaces."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass

from app.models.base import GenerateRequest, ProviderCapabilities, ProviderError, TraceMetadata


@dataclass
class StaticProviderAdapter:
    """Adapter implementation with deterministic local behavior for tests/runtime."""

    name: str
    api_key_env: str
    capabilities: ProviderCapabilities

    def _require_key(self) -> None:
        if not os.getenv(self.api_key_env):
            raise ProviderError(f"{self.name} is not configured: missing {self.api_key_env}")

    def generate(self, request: GenerateRequest, trace: TraceMetadata) -> dict[str, str | dict[str, str]]:
        self._require_key()
        content = request.messages[-1].get("content", "") if request.messages else ""
        return {
            "provider": self.name,
            "content": f"{self.name} response to: {content}",
            "trace": {
                "run_id": trace.run_id,
                "agent_id": trace.agent_id,
                "task_type": trace.task_type,
            },
        }

    def embed(self, texts: list[str], trace: TraceMetadata) -> list[list[float]]:
        self._require_key()
        if not self.capabilities.embeddings:
            raise ProviderError(f"{self.name} does not support embeddings")
        vectors: list[list[float]] = []
        for text in texts:
            digest = hashlib.sha256(f"{self.name}:{text}:{trace.run_id}".encode("utf-8")).digest()[:8]
            vectors.append([byte / 255 for byte in digest])
        return vectors

    def healthcheck(self) -> bool:
        return bool(os.getenv(self.api_key_env))


def build_provider_adapters() -> dict[str, StaticProviderAdapter]:
    """Create all known provider adapters using a normalized interface."""

    return {
        "groq": StaticProviderAdapter(
            name="groq",
            api_key_env="GROQ_API_KEY",
            capabilities=ProviderCapabilities(chat=True, tool_calling=True, embeddings=False),
        ),
        "openai": StaticProviderAdapter(
            name="openai",
            api_key_env="OPENAI_API_KEY",
            capabilities=ProviderCapabilities(chat=True, tool_calling=True, embeddings=True),
        ),
        "gemini": StaticProviderAdapter(
            name="gemini",
            api_key_env="GEMINI_API_KEY",
            capabilities=ProviderCapabilities(chat=True, tool_calling=True, embeddings=True),
        ),
        "anthropic": StaticProviderAdapter(
            name="anthropic",
            api_key_env="ANTHROPIC_API_KEY",
            capabilities=ProviderCapabilities(chat=True, tool_calling=True, embeddings=False),
        ),
    }

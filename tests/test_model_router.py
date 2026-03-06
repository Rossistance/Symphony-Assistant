from __future__ import annotations

import os
import unittest

from app.config import ModelRouterConfig
from app.models.base import (
    GenerateRequest,
    ProviderCapabilities,
    ProviderError,
    TraceMetadata,
    TransientProviderError,
)
from app.models.discovery import discover_providers
from app.models.router import ModelRouter


class StubProvider:
    def __init__(self, name: str, capabilities: ProviderCapabilities, *, healthy: bool = True):
        self.name = name
        self.capabilities = capabilities
        self.healthy = healthy
        self.failures_remaining = 0

    def generate(self, request: GenerateRequest, trace: TraceMetadata):
        if self.failures_remaining > 0:
            self.failures_remaining -= 1
            raise TransientProviderError(f"{self.name} transient")
        return {"provider": self.name, "messages": request.messages, "trace": trace.run_id}

    def embed(self, texts: list[str], trace: TraceMetadata):
        if not self.capabilities.embeddings:
            raise ProviderError("no embeddings")
        return [[0.1] for _ in texts]

    def healthcheck(self) -> bool:
        return self.healthy


class TestProviderDiscovery(unittest.TestCase):
    def setUp(self):
        self.original_env = os.environ.copy()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.original_env)

    def test_discovers_only_configured_provider_keys(self):
        os.environ["GROQ_API_KEY"] = "x"
        os.environ["OPENAI_API_KEY"] = "x"
        settings = ModelRouterConfig(router_order=("groq", "openai", "anthropic"), max_retries=1)

        registry = discover_providers(settings)

        self.assertEqual(list(registry.providers.keys()), ["groq", "openai"])
        self.assertTrue(registry.capability_matrix["openai"]["embeddings"])
        self.assertFalse(registry.capability_matrix["groq"]["embeddings"])


class TestModelRouterFailover(unittest.TestCase):
    def _trace(self) -> TraceMetadata:
        return TraceMetadata(run_id="run-1", agent_id="agent-a", task_type="chat", budget_context={"tokens": 500})

    def test_uses_router_precedence(self):
        groq = StubProvider("groq", ProviderCapabilities(chat=True, tool_calling=True, embeddings=False))
        openai = StubProvider("openai", ProviderCapabilities(chat=True, tool_calling=True, embeddings=True))
        registry = type(
            "Registry",
            (),
            {
                "providers": {"groq": groq, "openai": openai},
                "capability_matrix": {
                    "groq": {"chat": True, "tool_calling": True, "embeddings": False},
                    "openai": {"chat": True, "tool_calling": True, "embeddings": True},
                },
            },
        )()

        router = ModelRouter(settings=ModelRouterConfig(router_order=("groq", "openai"), max_retries=1), registry=registry, sleeper=lambda _: None)
        result = router.generate(GenerateRequest(messages=[{"role": "user", "content": "hi"}]), self._trace())

        self.assertEqual(result["provider"], "groq")

    def test_fails_over_after_transient_retries(self):
        groq = StubProvider("groq", ProviderCapabilities(chat=True, tool_calling=True, embeddings=False))
        groq.failures_remaining = 3
        openai = StubProvider("openai", ProviderCapabilities(chat=True, tool_calling=True, embeddings=True))
        registry = type(
            "Registry",
            (),
            {
                "providers": {"groq": groq, "openai": openai},
                "capability_matrix": {
                    "groq": {"chat": True, "tool_calling": True, "embeddings": False},
                    "openai": {"chat": True, "tool_calling": True, "embeddings": True},
                },
            },
        )()

        router = ModelRouter(settings=ModelRouterConfig(router_order=("groq", "openai"), max_retries=2), registry=registry, sleeper=lambda _: None)
        result = router.generate(GenerateRequest(messages=[{"role": "user", "content": "fallback"}]), self._trace())

        self.assertEqual(result["provider"], "openai")

    def test_embeddings_route_to_embedding_capable_provider(self):
        groq = StubProvider("groq", ProviderCapabilities(chat=True, tool_calling=True, embeddings=False))
        openai = StubProvider("openai", ProviderCapabilities(chat=True, tool_calling=True, embeddings=True))
        registry = type(
            "Registry",
            (),
            {
                "providers": {"groq": groq, "openai": openai},
                "capability_matrix": {
                    "groq": {"chat": True, "tool_calling": True, "embeddings": False},
                    "openai": {"chat": True, "tool_calling": True, "embeddings": True},
                },
            },
        )()

        router = ModelRouter(settings=ModelRouterConfig(router_order=("groq", "openai"), max_retries=1), registry=registry, sleeper=lambda _: None)
        vectors = router.embed(["hello"], self._trace())

        self.assertEqual(vectors, [[0.1]])


if __name__ == "__main__":
    unittest.main()

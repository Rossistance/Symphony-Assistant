from __future__ import annotations

import os
import unittest
import warnings

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
from app.services.model_routing import RoutedModelClient


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
        return {"provider": self.name, "content": request.messages[-1].get("content", ""), "trace": trace.run_id}

    def embed(self, texts: list[str], trace: TraceMetadata):
        del trace
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

    def _registry(self, *, groq_failures: int = 0):
        groq = StubProvider("groq", ProviderCapabilities(chat=True, tool_calling=True, embeddings=False))
        groq.failures_remaining = groq_failures
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
        return registry, groq, openai

    def test_uses_router_precedence(self):
        registry, _, _ = self._registry()
        router = ModelRouter(settings=ModelRouterConfig(router_order=("groq", "openai"), max_retries=1), registry=registry, sleeper=lambda _: None)

        result = router.generate(GenerateRequest(messages=[{"role": "user", "content": "hi"}]), self._trace())

        self.assertEqual(result["provider"], "groq")

    def test_fails_over_after_transient_retries_with_telemetry(self):
        registry, groq, openai = self._registry(groq_failures=3)
        router = ModelRouter(
            settings=ModelRouterConfig(router_order=("groq", "openai"), max_retries=2),
            registry=registry,
            sleeper=lambda _: None,
            random_fn=lambda: 0,
        )

        result = router.generate(GenerateRequest(messages=[{"role": "user", "content": "fallback"}]), self._trace())

        self.assertEqual(result["provider"], "openai")
        self.assertEqual(groq.failures_remaining, 0)
        self.assertEqual(openai.name, "openai")
        self.assertEqual([event.event_type for event in router.events], ["provider.route.selected", "provider.route.fallback", "provider.route.selected"])
        telemetry = router.telemetry_log[-1]
        self.assertEqual(telemetry.final_status, "success")
        self.assertEqual(telemetry.provider_selected, "openai")
        self.assertEqual(len(telemetry.fallback_transitions), 1)
        self.assertEqual(telemetry.fallback_transitions[0].from_provider, "groq")
        self.assertEqual(telemetry.fallback_transitions[0].to_provider, "openai")

    def test_embeddings_route_to_embedding_capable_provider(self):
        registry, _, _ = self._registry()
        router = ModelRouter(settings=ModelRouterConfig(router_order=("groq", "openai"), max_retries=1), registry=registry, sleeper=lambda _: None)

        vectors = router.embed(["hello"], self._trace())

        self.assertEqual(vectors, [[0.1]])


class TestLegacyRoutingCompatibility(unittest.TestCase):
    def test_routed_model_client_uses_compatibility_layer(self):
        provider = StubProvider("groq", ProviderCapabilities(chat=True, tool_calling=True, embeddings=False))
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            client = RoutedModelClient(providers={"groq": provider}, max_attempts=1)

        result = client.generate(model="llama", prompt="hello", provider="groq")

        self.assertIn("deprecated", str(caught[-1].message).lower())
        self.assertEqual(result.output_text, "hello")
        self.assertEqual(client.telemetry_log[-1].final_status, "success")


if __name__ == "__main__":
    unittest.main()

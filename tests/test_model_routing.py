import unittest

from app.services.model_routing import (
    ModelResponse,
    RoutedModelClient,
    TransientProviderError,
)


class FakeProvider:
    def __init__(self, name, outcomes):
        self.name = name
        self.outcomes = list(outcomes)
        self.calls = 0

    def generate(self, *, model: str, prompt: str) -> ModelResponse:
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class RoutedModelClientTests(unittest.TestCase):
    def test_retries_and_fallback_to_secondary_provider(self):
        primary = FakeProvider(
            "primary",
            [
                TransientProviderError("timeout"),
                TransientProviderError("429"),
            ],
        )
        fallback = FakeProvider(
            "fallback",
            [ModelResponse(output_text="ok", tokens_in=100, tokens_out=25, estimated_cost=0.012)],
        )

        sleeps = []
        client = RoutedModelClient(
            providers={"primary": primary, "fallback": fallback},
            fallback_map={"primary": "fallback"},
            max_attempts=2,
            base_backoff_seconds=1,
            jitter_ratio=0.5,
            sleep_fn=lambda value: sleeps.append(value),
            random_fn=lambda: 1,
        )

        response = client.generate(model="gpt-x", prompt="hello", provider="primary")

        self.assertEqual(response.output_text, "ok")
        self.assertEqual(primary.calls, 2)
        self.assertEqual(fallback.calls, 1)
        self.assertEqual(sleeps, [1.5])

        event_types = [event.event_type for event in client.events]
        self.assertEqual(
            event_types,
            [
                "provider.route.selected",
                "provider.route.fallback",
                "provider.route.selected",
            ],
        )

        telemetry = client.telemetry_log[-1]
        self.assertEqual(telemetry.provider_selected, "fallback")
        self.assertEqual(telemetry.model, "gpt-x")
        self.assertEqual(telemetry.tokens_in, 100)
        self.assertEqual(telemetry.tokens_out, 25)
        self.assertEqual(telemetry.estimated_cost, 0.012)
        self.assertEqual(telemetry.final_status, "success")
        self.assertEqual(len(telemetry.fallback_transitions), 1)
        self.assertEqual(telemetry.fallback_transitions[0].from_provider, "primary")
        self.assertEqual(telemetry.fallback_transitions[0].to_provider, "fallback")

    def test_failure_when_all_attempts_and_fallback_exhausted(self):
        primary = FakeProvider(
            "primary",
            [TransientProviderError("timeout"), TransientProviderError("timeout")],
        )
        client = RoutedModelClient(
            providers={"primary": primary},
            max_attempts=2,
            sleep_fn=lambda _: None,
            random_fn=lambda: 0,
        )

        with self.assertRaises(TransientProviderError):
            client.generate(model="gpt-x", prompt="hello", provider="primary")

        telemetry = client.telemetry_log[-1]
        self.assertEqual(telemetry.provider_selected, "primary")
        self.assertEqual(telemetry.final_status, "failed")
        self.assertEqual(telemetry.tokens_in, 0)
        self.assertEqual(telemetry.tokens_out, 0)
        self.assertEqual(telemetry.estimated_cost, 0)


if __name__ == "__main__":
    unittest.main()

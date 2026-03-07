from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from app.api.approvals import (
    APPROVAL_DECISION_PERMISSION,
    AuthenticatedActorContext,
    DECISION_ROUTE,
    post_approval_decision,
)
from app.config import DeliverablesConfig
from app.api.http_surfaces import (
    DAILY_SUGGESTION_ROUTE,
    ORCHESTRATE_JOB_ROUTE,
    TASK_DETAILS_ROUTE,
    WHATSAPP_WEBHOOK_ROUTE,
    HttpSurfaceHandlers,
)
from app.messaging.agent_bus import AgentMessageBroker
from app.messaging.base import InboundMessage
from app.messaging.runtime_state import get_runtime_state_store
from app.messaging.state_store import SqliteHttpSurfaceStateStore
from app.services.agent_runtime import AgentRuntime
from app.services.deliverables import DeliverableArtifact, DeliverablePublisherError, InMemoryDeliverablePublisher
from app.services.model_routing import ModelResponse, RoutedModelClient, TransientProviderError
from app.services.policy_engine import ActionPolicyEngine, ActionRequest


class FakeProvider:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def generate(self, *, model: str, prompt: str) -> ModelResponse:
        self.calls += 1
        result = self.outcomes.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class FailingDeliverablePublisher:
    def publish(self, *, task_id: str, artifacts: list[DeliverableArtifact]):
        raise DeliverablePublisherError("simulated publish outage")


class AcceptanceHttpSurfaceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        import os

        os.environ["MESSAGING_STATE_DB_PATH"] = str(Path(self.temp_dir.name) / "messaging_state.db")
        get_runtime_state_store.cache_clear()
        self.handlers = HttpSurfaceHandlers(
            runtime=AgentRuntime(clock=lambda: 0),
            message_bus=AgentMessageBroker(),
            tasks=SqliteHttpSurfaceStateStore(Path(self.temp_dir.name) / "http_surface_state.db"),
            event_store=SqliteHttpSurfaceStateStore(Path(self.temp_dir.name) / "http_surface_state.db"),
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_contract_routes_exist(self):
        self.assertEqual(WHATSAPP_WEBHOOK_ROUTE, "/webhooks/whatsapp")
        self.assertEqual(ORCHESTRATE_JOB_ROUTE, "/jobs/orchestrate")
        self.assertEqual(DAILY_SUGGESTION_ROUTE, "/jobs/daily-suggestion")
        self.assertEqual(TASK_DETAILS_ROUTE, "/api/v1/tasks/:id")
        self.assertEqual(DECISION_ROUTE, "/api/v1/approvals/:id/decision")

    def test_deliverable_return_and_task_lookup(self):
        response = self.handlers.post_jobs_orchestrate(
            {
                "task_id": "task-deliverable",
                "prompt": "draft release summary",
                "deliverable": "Release summary v1",
                "subtasks": [],
            }
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            set(response.body.keys()),
            {
                "task_id",
                "status",
                "deliverable",
                "deliverables",
                "completion_message",
                "execution_mode",
                "delegation_mode",
                "delegation_stages",
            },
        )
        self.assertEqual(list(response.body.keys()).count("completion_message"), 1)
        self.assertEqual(response.body["deliverable"], "Release summary v1")
        self.assertEqual(len(response.body["deliverables"]), 1)
        self.assertTrue(response.body["deliverables"][0]["share_url"].startswith("https://drive.example/files/"))
        self.assertIsNone(response.body["deliverables"][0]["parent_folder_id"])
        self.assertEqual(response.body["deliverables"][0]["access_mode"], "view_only")
        self.assertEqual(response.body["deliverables"][0]["permission_role"], "reader")
        self.assertEqual(response.body["deliverables"][0]["permission_type"], "anyone")
        self.assertIn("Done — I completed draft release summary.", response.body["completion_message"])

        lookup = self.handlers.get_api_v1_tasks_id("task-deliverable")
        self.assertEqual(lookup.status_code, 200)
        self.assertEqual(lookup.body["task_id"], "task-deliverable")


    def test_defaults_deliverable_publisher_from_config(self):
        handlers = HttpSurfaceHandlers(
            runtime=AgentRuntime(clock=lambda: 0),
            message_bus=AgentMessageBroker(),
            tasks=SqliteHttpSurfaceStateStore(Path(self.temp_dir.name) / "http_surface_state.db"),
            event_store=SqliteHttpSurfaceStateStore(Path(self.temp_dir.name) / "http_surface_state.db"),
            deliverables_config=DeliverablesConfig(
                backend="in_memory",
                in_memory_drive_root="https://drive.configured/files",
                google_drive_folder_id="",
            ),
        )

        self.assertIsInstance(handlers.deliverable_publisher, InMemoryDeliverablePublisher)
        assert handlers.deliverable_publisher is not None
        self.assertEqual(handlers.deliverable_publisher.drive_root, "https://drive.configured/files")

    def test_orchestrate_returns_actionable_error_when_drive_credentials_missing(self):
        import os

        os.environ.pop("GOOGLE_DRIVE_CREDENTIALS_JSON", None)
        handlers = HttpSurfaceHandlers(
            runtime=AgentRuntime(clock=lambda: 0),
            message_bus=AgentMessageBroker(),
            tasks=SqliteHttpSurfaceStateStore(Path(self.temp_dir.name) / "http_surface_state.db"),
            event_store=SqliteHttpSurfaceStateStore(Path(self.temp_dir.name) / "http_surface_state.db"),
            deliverables_config=DeliverablesConfig(
                backend="google_drive",
                in_memory_drive_root="https://drive.example/files",
                google_drive_folder_id="folder-123",
            ),
        )

        response = handlers.post_jobs_orchestrate({"task_id": "task-cred", "prompt": "draft plan"})

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.body["error"], "deliverable_publish_credential_error")
        failed_event = next(event for event in handlers.events if event["event_type"] == "deliverable.publish.failed")
        self.assertEqual(failed_event["payload"]["error_type"], "credentials")
        self.assertEqual(failed_event["payload"]["reason_code"], "deliverable_publish_credential_error")
        self.assertEqual(failed_event["payload"]["correlation_id"], "task-cred")
        self.assertEqual(handlers.events[-1]["event_type"], "task.status.updated")

    def test_orchestrate_publish_failure_tracks_failed_status_without_false_completion_reply(self):
        handlers = HttpSurfaceHandlers(
            runtime=AgentRuntime(clock=lambda: 0),
            message_bus=AgentMessageBroker(),
            tasks=SqliteHttpSurfaceStateStore(Path(self.temp_dir.name) / "http_surface_state.db"),
            event_store=SqliteHttpSurfaceStateStore(Path(self.temp_dir.name) / "http_surface_state.db"),
            deliverable_publisher=FailingDeliverablePublisher(),
        )

        response = handlers.post_jobs_orchestrate({"task_id": "task-publish-fail", "prompt": "draft plan"})

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.body["status"], "partial")
        self.assertEqual(response.body["error"], "deliverable_publish_failed")
        self.assertEqual(response.body["failure_reason_code"], "deliverable_publish_failed")
        self.assertIn("couldn't publish", response.body["completion_message"].lower())

        event_types = [event["event_type"] for event in handlers.events]
        self.assertIn("deliverable.publish.failed", event_types)
        self.assertIn("task.status.updated", event_types)
        self.assertNotIn("message.reply.sent", event_types)

        failed_event = next(event for event in handlers.events if event["event_type"] == "deliverable.publish.failed")
        self.assertEqual(failed_event["payload"]["reason_code"], "deliverable_publish_failed")
        self.assertEqual(failed_event["payload"]["correlation_id"], "task-publish-fail")

        status_event = next(event for event in handlers.events if event["event_type"] == "task.status.updated")
        self.assertEqual(status_event["payload"]["status"], "partial")
        self.assertEqual(status_event["payload"]["correlation_id"], "task-publish-fail")


    def test_swarm_spawn_and_message_synthesis_events(self):
        response = self.handlers.post_jobs_orchestrate(
            {
                "task_id": "task-swarm",
                "prompt": "coordinate research",
                "subtasks": [
                    {"task_id": "a", "objective": "collect docs", "dependencies": []},
                    {"task_id": "b", "objective": "summarize docs", "dependencies": ["a"]},
                ],
            }
        )

        self.assertEqual(response.status_code, 200)
        runtime_events = [event.event_type for event in self.handlers.runtime.events]
        self.assertIn("agent.spawned", runtime_events)
        self.assertIn("execution.mode.selected", runtime_events)

        event_types = [event["event_type"] for event in self.handlers.events]
        self.assertIn("agent.message.sent", event_types)
        self.assertIn("deliverable.published", event_types)
        self.assertIn("message.reply.sent", event_types)
        self.assertIn("task.analysis.completed", event_types)
        deliverable_event = next(event for event in self.handlers.events if event["event_type"] == "deliverable.published")
        self.assertIn("parent_folder_id", deliverable_event["payload"])
        self.assertIn("access_mode", deliverable_event["payload"])

    def test_mode_selection_by_dependency_profile_and_slow_mode_trigger(self):
        response = self.handlers.post_jobs_orchestrate(
            {
                "task_id": "task-mode",
                "prompt": "this is a hard problem; let's take it slow",
                "dependency_graph_density": 0.8,
                "decomposition_count_estimate": 3,
                "subtasks": [{"task_id": "a", "objective": "step"}],
            }
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.body["execution_mode"], "slow")
        self.assertEqual(response.body["delegation_mode"], "SEQUENTIAL")

    def test_whatsapp_webhook_surface_happy_path(self):
        response = self.handlers.post_webhooks_whatsapp({"MessageSid": "SM1", "From": "+1555", "Body": "Hi"})

        self.assertEqual(response.status_code, 202)
        self.assertTrue(response.body["accepted"])
        self.assertEqual(response.body["provider_message_id"], "SM1")
        self.assertEqual(self.handlers.events[-1]["event_type"], "message.reply.sent")

    def test_whatsapp_webhook_surface_rejects_missing_required_fields(self):
        before_events = list(self.handlers.events)

        response = self.handlers.post_webhooks_whatsapp({"MessageSid": "SM1", "Body": "Hi"})

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.body["error"]["code"], "invalid_webhook_payload")
        self.assertEqual(
            response.body["error"]["details"],
            [{"field": "from_user", "issue": "required"}],
        )
        self.assertEqual(self.handlers.events, before_events)

    def test_whatsapp_webhook_surface_rejects_invalid_types(self):
        before_events = list(self.handlers.events)

        response = self.handlers.post_webhooks_whatsapp({"MessageSid": 123, "From": "+1555", "Body": "Hi"})

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.body["error"]["code"], "invalid_webhook_payload")
        self.assertEqual(
            response.body["error"]["details"],
            [{"field": "MessageSid", "issue": "must_be_string"}],
        )
        self.assertEqual(self.handlers.events, before_events)

    def test_whatsapp_webhook_surface_rejects_unknown_fields(self):
        before_events = list(self.handlers.events)

        response = self.handlers.post_webhooks_whatsapp(
            {"MessageSid": "SM1", "From": "+1555", "Body": "Hi", "unexpected": "value"}
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.body["error"]["code"], "invalid_webhook_payload")
        self.assertEqual(
            response.body["error"]["details"],
            [{"field": "unexpected", "issue": "unknown_field"}],
        )
        self.assertEqual(self.handlers.events, before_events)

    def test_whatsapp_webhook_surface_dedupes_replays(self):
        first = self.handlers.post_webhooks_whatsapp({"MessageSid": "SM1", "From": "+1555", "Body": "Hi"})
        second = self.handlers.post_webhooks_whatsapp({"MessageSid": "SM1", "From": "+1555", "Body": "Hi"})

        self.assertEqual(first.status_code, 202)
        self.assertEqual(second.status_code, 202)
        self.assertTrue(second.body["duplicate"])
        event_types = [event["event_type"] for event in self.handlers.events]
        self.assertIn("message.inbound.accepted", event_types)
        self.assertIn("message.inbound.duplicate", event_types)

    def test_gateway_socket_callback_uses_shared_pipeline(self):
        inbound = InboundMessage(
            channel="whatsapp",
            from_user="+1555",
            body="Hi",
            provider_message_id="SM-GW-1",
            provider_thread_id="thread-1",
        )

        first = self.handlers.ingest_gateway_inbound(inbound)
        replay = self.handlers.ingest_gateway_inbound(inbound)

        self.assertEqual(first.status_code, 202)
        self.assertTrue(first.body["accepted"])
        self.assertFalse(first.body["duplicate"])
        self.assertEqual(replay.status_code, 202)
        self.assertFalse(replay.body["accepted"])
        self.assertTrue(replay.body["duplicate"])


    def test_restart_continuity_reloads_tasks_and_events_from_same_db(self):
        db_path = Path(self.temp_dir.name) / "continuity_http_surface.db"
        first = HttpSurfaceHandlers(
            runtime=AgentRuntime(clock=lambda: 0),
            message_bus=AgentMessageBroker(),
            tasks=SqliteHttpSurfaceStateStore(db_path),
            event_store=SqliteHttpSurfaceStateStore(db_path),
        )

        created = first.post_jobs_orchestrate({"task_id": "task-restart", "prompt": "prepare update"})
        self.assertEqual(created.status_code, 200)
        self.assertTrue(any(event["event_type"] == "deliverable.published" for event in first.events))

        reloaded = HttpSurfaceHandlers(
            runtime=AgentRuntime(clock=lambda: 0),
            message_bus=AgentMessageBroker(),
            tasks=SqliteHttpSurfaceStateStore(db_path),
            event_store=SqliteHttpSurfaceStateStore(db_path),
        )

        lookup = reloaded.get_api_v1_tasks_id("task-restart")
        self.assertEqual(lookup.status_code, 200)
        self.assertEqual(lookup.body["status"], "completed")
        self.assertTrue(any(event["event_type"] == "deliverable.published" for event in reloaded.events))

    def test_daily_suggestion_surface(self):
        response = self.handlers.post_jobs_daily_suggestion({"user": "alex"})

        self.assertEqual(response.status_code, 200)
        self.assertIn("deliverable", response.body)


class AcceptanceProviderRoutingTests(unittest.TestCase):
    def test_groq_precedence(self):
        groq = FakeProvider([ModelResponse("groq result", 10, 5, 0.001)])
        openai = FakeProvider([ModelResponse("openai result", 10, 5, 0.002)])
        client = RoutedModelClient(providers={"groq": groq, "openai": openai}, fallback_map={"groq": "openai"})

        result = client.generate(model="llama", prompt="hi", provider="groq")

        self.assertEqual(result.output_text, "groq result")
        self.assertEqual(groq.calls, 1)
        self.assertEqual(openai.calls, 0)

    def test_groq_timeout_fallback(self):
        groq = FakeProvider([TransientProviderError("timeout"), TransientProviderError("timeout")])
        openai = FakeProvider([ModelResponse("fallback", 8, 4, 0.001)])
        client = RoutedModelClient(
            providers={"groq": groq, "openai": openai},
            fallback_map={"groq": "openai"},
            max_attempts=2,
            sleep_fn=lambda _: None,
        )

        result = client.generate(model="llama", prompt="hi", provider="groq")

        self.assertEqual(result.output_text, "fallback")
        self.assertEqual(groq.calls, 2)
        self.assertEqual(openai.calls, 1)
        self.assertIn("provider.route.fallback", [event.event_type for event in client.events])


class AcceptanceApprovalDecisionTests(unittest.TestCase):
    def test_approval_decision_endpoint(self):
        engine = ActionPolicyEngine()
        enforcement = engine.enforce(ActionRequest(action_id="act-1", action_type="credential_change", description="rotate"))

        response = post_approval_decision(
            approval_id=enforcement.approval_id or "",
            payload={
                "actor": "ops",
                "decision": "approved",
            },
            authenticated_actor=AuthenticatedActorContext(
                subject="ops",
                authenticated=True,
                permissions=frozenset({APPROVAL_DECISION_PERMISSION}),
            ),
            policy_engine=engine,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.body["status"], "approved")


if __name__ == "__main__":
    unittest.main()

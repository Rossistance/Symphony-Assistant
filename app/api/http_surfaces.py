"""HTTP surface handlers for orchestration jobs, tasks, and webhooks."""

from __future__ import annotations

import os
from pathlib import Path
from dataclasses import dataclass, field
from typing import Any

from app.config import DeliverablesConfig
from app.messaging.agent_bus import AgentMessageBroker
from app.messaging.base import InboundMessage
from app.messaging.inbound_pipeline import InboundIngestionPipeline
from app.messaging.runtime_state import get_runtime_state_store
from app.messaging.state_store import (
    EventRecordStore,
    InMemoryEventRecordStore,
    InMemoryTaskRecordStore,
    TaskRecordStore,
)
from app.services.deliverables import (
    DeliverableArtifact,
    DeliverablePublisher,
    DeliverablePublisherError,
    DeliverablePublisherConfigError,
    DeliverablePublisherCredentialError,
    create_deliverable_publisher,
    compose_completion_message,
)
from app.services.agent_runtime import AgentRuntime, DelegationTask
from app.services.orchestration_policy import ExecutionMode as OrchestrationMode, detect_execution_mode
from app.webhooks.inbound import WebhookValidationError, parse_inbound_webhook

WHATSAPP_WEBHOOK_ROUTE = "/webhooks/whatsapp"
ORCHESTRATE_JOB_ROUTE = "/jobs/orchestrate"
DAILY_SUGGESTION_ROUTE = "/jobs/daily-suggestion"
TASK_DETAILS_ROUTE = "/api/v1/tasks/:id"


def normalize_event_type(event_type: str) -> str:
    """Normalize event names to canonical lower-dot taxonomy format."""

    return ".".join(part for part in event_type.strip().lower().replace("_", ".").split(".") if part)


@dataclass(frozen=True)
class ApiResponse:
    status_code: int
    body: dict[str, Any]


@dataclass
class HttpSurfaceHandlers:
    """Pure handlers that can be adapted to any HTTP framework."""

    runtime: AgentRuntime
    message_bus: AgentMessageBroker
    tasks: TaskRecordStore = field(default_factory=InMemoryTaskRecordStore)
    deliverable_publisher: DeliverablePublisher | None = None
    deliverables_config: DeliverablesConfig = field(default_factory=DeliverablesConfig)
    inbound_pipeline: InboundIngestionPipeline | None = None
    event_store: EventRecordStore = field(default_factory=InMemoryEventRecordStore)

    def __post_init__(self) -> None:
        if self.deliverable_publisher is None:
            self.deliverable_publisher = create_deliverable_publisher(config=self.deliverables_config)
        if self.inbound_pipeline is None:
            self.inbound_pipeline = InboundIngestionPipeline(
                dedupe_store=get_runtime_state_store(),
                event_emitter=self._emit,
            )

    @property
    def events(self) -> list[dict[str, Any]]:
        return self.event_store.list()

    def _emit(self, event_type: str, payload: dict[str, Any]) -> None:
        self.event_store.append(normalize_event_type(event_type), payload)

    def _materialize_artifacts(
        self,
        *,
        task_id: str,
        deliverable: str,
        artifacts: list[DeliverableArtifact],
    ) -> list[DeliverableArtifact]:
        """Ensure local artifact files exist in the repository deliverables folder."""

        output_root = Path(os.getenv("DELIVERABLES_LOCAL_OUTPUT_DIR", "deliverables"))
        task_dir = output_root / task_id
        task_dir.mkdir(parents=True, exist_ok=True)

        materialized: list[DeliverableArtifact] = []
        for artifact in artifacts:
            source = Path(artifact.source_ref)
            if source.exists():
                materialized.append(artifact)
                continue

            local_name = source.name or f"{artifact.artifact_id}.txt"
            local_path = task_dir / local_name
            if not local_path.exists():
                local_path.write_text(deliverable, encoding="utf-8")

            materialized.append(
                DeliverableArtifact(
                    artifact_id=artifact.artifact_id,
                    title=artifact.title,
                    mime_type=artifact.mime_type,
                    source_ref=str(local_path),
                )
            )
        return materialized

    def _publish_failure_response(
        self,
        *,
        task_id: str,
        deliverable: str,
        mode: OrchestrationMode,
        delegation_mode: str,
        plan_stages: list[list[str]],
        reason_code: str,
        error: str,
        error_type: str,
        error_message: str,
        status_code: int = 500,
        final_status: str = "failed",
    ) -> ApiResponse:
        status = final_status
        self._emit(
            "deliverable.publish.failed",
            {
                "task_id": task_id,
                "correlation_id": task_id,
                "status": status,
                "reason_code": reason_code,
                "error_type": error_type,
                "error_message": error_message,
            },
        )
        self._emit(
            "task.status.updated",
            {
                "task_id": task_id,
                "correlation_id": task_id,
                "status": status,
                "reason_code": reason_code,
            },
        )
        task_payload = {
            "task_id": task_id,
            "status": status,
            "deliverable": deliverable,
            "deliverables": [],
            "completion_message": "I couldn't publish your deliverable. Please retry after fixing the publish issue.",
            "execution_mode": mode.value,
            "delegation_mode": delegation_mode,
            "delegation_stages": plan_stages,
            "failure_reason_code": reason_code,
            "error": error,
            "message": error_message,
        }
        self.tasks.put(task_id, task_payload)
        return ApiResponse(status_code=status_code, body=task_payload)

    def post_webhooks_whatsapp(self, payload: dict[str, Any]) -> ApiResponse:
        try:
            inbound = parse_inbound_webhook(payload, channel="whatsapp")
        except WebhookValidationError as exc:
            return ApiResponse(status_code=422, body=exc.to_response_body())

        response = self.ingest_gateway_inbound(inbound, source="webhook.whatsapp")
        if response.body["duplicate"]:
            return response

        reply_text = "Received. Processing your request."
        self._emit(
            "message.reply.sent",
            {
                "channel": inbound.channel,
                "to": inbound.from_user,
                "provider_thread_id": inbound.provider_thread_id,
                "text": reply_text,
            },
        )
        return response

    def ingest_gateway_inbound(self, inbound: InboundMessage, *, source: str = "gateway.socket") -> ApiResponse:
        """Accept normalized inbound events from webhook handlers or gateway callbacks."""

        assert self.inbound_pipeline is not None
        ingestion_result = self.inbound_pipeline.ingest(inbound, source=source)
        return ApiResponse(
            status_code=202,
            body={
                "accepted": ingestion_result.accepted,
                "duplicate": not ingestion_result.accepted,
                "provider_message_id": inbound.provider_message_id,
                "provider_thread_id": inbound.provider_thread_id,
                "dedupe_key": ingestion_result.dedupe_key,
            },
        )

    def post_jobs_orchestrate(self, payload: dict[str, Any]) -> ApiResponse:
        prompt = str(payload.get("prompt", "")).strip()
        if not prompt:
            return ApiResponse(status_code=400, body={"error": "prompt is required"})

        task_id = str(payload.get("task_id") or f"task-{self.tasks.next_task_sequence()}")
        lead_id = f"lead-{task_id}"
        self.runtime.register_lead_agent(
            agent_id=lead_id,
            role="lead",
            objective=prompt,
            constraints=["follow policy", "return concise result"],
            output_contract="deliverable",
            correlation_id=task_id,
            safety_scope={"task_id": task_id},
            max_tool_calls=20,
            max_runtime_sec=300,
            execution_mode=detect_execution_mode(prompt),
        )

        analysis = self.runtime.compute_task_analysis_inputs(
            decomposition_count_estimate=int(payload.get("decomposition_count_estimate", 4)),
            dependency_graph_density=float(payload.get("dependency_graph_density", 0.2)),
            urgency=str(payload.get("urgency", "normal")),
            token_budget=int(payload.get("token_budget", 4000)),
            tool_latency_profile=str(payload.get("tool_latency_profile", "mixed")),
        )
        self._emit("task.analysis.completed", {"task_id": task_id, "analysis": analysis.__dict__})

        subtasks_payload = payload.get("subtasks") or []
        tasks = [
            DelegationTask(
                task_id=str(item["task_id"]),
                role=str(item.get("role", "worker")),
                objective=str(item["objective"]),
                constraints=list(item.get("constraints") or ["no policy bypass"]),
                max_tool_calls=int(item.get("max_tool_calls", 3)),
                max_runtime_sec=int(item.get("max_runtime_sec", 60)),
                dependencies=list(item.get("dependencies") or []),
            )
            for item in subtasks_payload
        ]
        plan = self.runtime.orchestrate_delegation(parent_agent_id=lead_id, tasks=tasks, task_analysis=analysis)

        for stage in plan.stages:
            for child_id in stage:
                msg = self.message_bus.send_agent_message(
                    from_agent=lead_id,
                    to_agent=child_id,
                    topic="task.assignment",
                    payload_ref=f"task://{task_id}/{child_id}",
                    correlation_id=task_id,
                )
                self._emit("agent.message.sent", {"task_id": task_id, "message_id": msg.message_id})

        deliverable = str(payload.get("deliverable") or f"Deliverable for: {prompt}")

        artifacts_payload = payload.get("artifacts") or []
        artifacts: list[DeliverableArtifact] = []
        for index, item in enumerate(artifacts_payload, start=1):
            source_ref = str(item.get("source_ref") or f"deliverable-{index}.txt").strip()
            artifacts.append(
                DeliverableArtifact(
                    artifact_id=str(item.get("artifact_id") or f"artifact-{index}"),
                    title=str(item.get("title") or f"Deliverable {index}"),
                    mime_type=str(item.get("mime_type") or "text/plain"),
                    source_ref=source_ref,
                )
            )

        if not artifacts:
            artifacts = [
                DeliverableArtifact(
                    artifact_id="artifact-1",
                    title="Primary Deliverable",
                    mime_type="text/plain",
                    source_ref="deliverable.txt",
                )
            ]

        artifacts = self._materialize_artifacts(task_id=task_id, deliverable=deliverable, artifacts=artifacts)

        assert self.deliverable_publisher is not None
        mode = detect_execution_mode(prompt)

        try:
            published = self.deliverable_publisher.publish(task_id=task_id, artifacts=artifacts)
        except DeliverablePublisherConfigError as exc:
            return self._publish_failure_response(
                task_id=task_id,
                deliverable=deliverable,
                mode=mode,
                delegation_mode=plan.mode.value,
                plan_stages=plan.stages,
                reason_code="deliverable_publish_config_error",
                error="deliverable_publish_config_error",
                error_type="config",
                error_message=str(exc),
                final_status="partial",
            )
        except DeliverablePublisherCredentialError as exc:
            return self._publish_failure_response(
                task_id=task_id,
                deliverable=deliverable,
                mode=mode,
                delegation_mode=plan.mode.value,
                plan_stages=plan.stages,
                reason_code="deliverable_publish_credential_error",
                error="deliverable_publish_credential_error",
                error_type="credentials",
                error_message=str(exc),
            )
        except DeliverablePublisherError as exc:
            return self._publish_failure_response(
                task_id=task_id,
                deliverable=deliverable,
                mode=mode,
                delegation_mode=plan.mode.value,
                plan_stages=plan.stages,
                reason_code="deliverable_publish_failed",
                error="deliverable_publish_failed",
                error_type="publish",
                error_message=str(exc),
                final_status="partial",
            )
        except Exception as exc:
            return self._publish_failure_response(
                task_id=task_id,
                deliverable=deliverable,
                mode=mode,
                delegation_mode=plan.mode.value,
                plan_stages=plan.stages,
                reason_code="deliverable_publish_unexpected_error",
                error="deliverable_publish_unexpected_error",
                error_type="unexpected",
                error_message=str(exc),
                final_status="partial",
            )
        for deliverable_item in published:
            self._emit(
                "deliverable.published",
                {
                    "task_id": task_id,
                    "artifact_id": deliverable_item.artifact_id,
                    "title": deliverable_item.title,
                    "drive_file_id": deliverable_item.drive_file_id,
                    "share_url": deliverable_item.share_url,
                    "parent_folder_id": deliverable_item.parent_folder_id,
                    "access_mode": deliverable_item.access_mode,
                    "share_visibility": deliverable_item.share_visibility,
                    "permission_role": deliverable_item.permission_role,
                    "permission_type": deliverable_item.permission_type,
                    "access_reference": deliverable_item.access_reference,
                    "expiry_requested_hours": deliverable_item.expiry_requested_hours,
                    "expiry_applied": deliverable_item.expiry_applied,
                },
            )

        completion_message = compose_completion_message(task_title=prompt, deliverables=published)
        self._emit(
            "message.reply.sent",
            {
                "channel": "whatsapp",
                "task_id": task_id,
                "text": completion_message,
                "deliverable_count": len(published),
            },
        )

        task_payload = {
            "task_id": task_id,
            "status": "completed",
            "deliverable": deliverable,
            "deliverables": [
                {
                    "artifact_id": item.artifact_id,
                    "title": item.title,
                    "mime_type": item.mime_type,
                    "drive_file_id": item.drive_file_id,
                    "share_url": item.share_url,
                    "parent_folder_id": item.parent_folder_id,
                    "access_mode": item.access_mode,
                    "share_visibility": item.share_visibility,
                    "permission_role": item.permission_role,
                    "permission_type": item.permission_type,
                    "access_reference": item.access_reference,
                    "expiry_requested_hours": item.expiry_requested_hours,
                    "expiry_applied": item.expiry_applied,
                }
                for item in published
            ],
            "completion_message": completion_message,
            "execution_mode": mode.value,
            "delegation_mode": plan.mode.value,
            "delegation_stages": plan.stages,
        }
        self.tasks.put(task_id, task_payload)
        return ApiResponse(status_code=200, body=self.tasks.get(task_id) or {})

    def post_jobs_daily_suggestion(self, payload: dict[str, Any]) -> ApiResponse:
        user = str(payload.get("user", "")).strip() or "user"
        prompt = payload.get("prompt") or f"Suggest one high-impact task for {user} today"
        return self.post_jobs_orchestrate({**payload, "prompt": prompt})

    def get_api_v1_tasks_id(self, task_id: str) -> ApiResponse:
        task = self.tasks.get(task_id)
        if task is None:
            return ApiResponse(status_code=404, body={"error": "task not found"})
        return ApiResponse(status_code=200, body=task)


__all__ = [
    "ApiResponse",
    "DAILY_SUGGESTION_ROUTE",
    "HttpSurfaceHandlers",
    "ORCHESTRATE_JOB_ROUTE",
    "TASK_DETAILS_ROUTE",
    "WHATSAPP_WEBHOOK_ROUTE",
    "normalize_event_type",
]

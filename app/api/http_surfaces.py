"""HTTP surface handlers for orchestration jobs, tasks, and webhooks."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import count
from typing import Any

from app.messaging.agent_bus import AgentMessageBroker
from app.services.agent_runtime import AgentRuntime, DelegationTask
from app.services.orchestration_policy import ExecutionMode as OrchestrationMode, detect_execution_mode
from app.webhooks.inbound import parse_inbound_webhook

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
class TaskStore:
    """In-memory task registry used by HTTP handlers and tests."""

    _tasks: dict[str, dict[str, Any]]

    @classmethod
    def create(cls) -> "TaskStore":
        return cls(_tasks={})

    def put(self, task_id: str, task: dict[str, Any]) -> None:
        self._tasks[task_id] = task

    def get(self, task_id: str) -> dict[str, Any] | None:
        return self._tasks.get(task_id)


@dataclass
class HttpSurfaceHandlers:
    """Pure handlers that can be adapted to any HTTP framework."""

    runtime: AgentRuntime
    message_bus: AgentMessageBroker
    tasks: TaskStore

    def __post_init__(self) -> None:
        self.events: list[dict[str, Any]] = []
        self._ids = count(1)

    def _emit(self, event_type: str, payload: dict[str, Any]) -> None:
        self.events.append({"event_type": normalize_event_type(event_type), "payload": payload})

    def post_webhooks_whatsapp(self, payload: dict[str, Any]) -> ApiResponse:
        inbound = parse_inbound_webhook(payload, channel="whatsapp")
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
        return ApiResponse(
            status_code=202,
            body={
                "accepted": True,
                "provider_message_id": inbound.provider_message_id,
                "provider_thread_id": inbound.provider_thread_id,
            },
        )

    def post_jobs_orchestrate(self, payload: dict[str, Any]) -> ApiResponse:
        prompt = str(payload.get("prompt", "")).strip()
        if not prompt:
            return ApiResponse(status_code=400, body={"error": "prompt is required"})

        task_id = str(payload.get("task_id") or f"task-{next(self._ids)}")
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
        self._emit("deliverable.published", {"task_id": task_id, "deliverable": deliverable})

        mode = detect_execution_mode(prompt)
        self.tasks.put(
            task_id,
            {
                "task_id": task_id,
                "status": "completed",
                "deliverable": deliverable,
                "execution_mode": mode.value,
                "delegation_mode": plan.mode.value,
                "delegation_stages": plan.stages,
            },
        )
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
    "TaskStore",
    "WHATSAPP_WEBHOOK_ROUTE",
    "normalize_event_type",
]

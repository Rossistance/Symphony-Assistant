"""Minimal Flask runtime entrypoint for HTTP handlers."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from flask import Flask, Response, jsonify, request

from app.api.approvals import AuthenticatedActorContext, post_approval_decision
from app.api.http_surfaces import HttpSurfaceHandlers
from app.api.simulator import SIMULATION_UI_HTML, SimulationStateStore
from app.config import DeliverablesConfig
from app.messaging.agent_bus import AgentMessageBroker
from app.messaging.state_store import SqliteHttpSurfaceStateStore
from app.services.agent_runtime import AgentRuntime
from app.services.policy_engine import ActionPolicyEngine


class ApiResponseLike(Protocol):
    """Structural contract for handler responses."""

    status_code: int
    body: dict[str, Any]


@dataclass(frozen=True)
class RuntimeContainer:
    """Dependency wiring for runtime handlers and stores."""

    http_handlers: HttpSurfaceHandlers
    policy_engine: ActionPolicyEngine
    simulation_store: SimulationStateStore = field(default_factory=SimulationStateStore)


def to_json_response(api_response: ApiResponseLike):
    """Convert handler ApiResponse objects into framework JSON responses."""

    return jsonify(api_response.body), api_response.status_code


def _build_actor_context() -> AuthenticatedActorContext:
    subject = request.headers.get("X-Actor-Id", "").strip()
    raw_permissions = request.headers.get("X-Actor-Permissions", "")
    permissions = frozenset(permission.strip() for permission in raw_permissions.split(",") if permission.strip())
    return AuthenticatedActorContext(
        subject=subject,
        authenticated=bool(subject),
        permissions=permissions,
    )


def wire_runtime_dependencies() -> RuntimeContainer:
    """Construct runtime dependencies from startup configuration."""

    state_db_path = Path(os.getenv("HTTP_SURFACE_STATE_DB_PATH", "runtime/http_surface_state.db"))
    state_store = SqliteHttpSurfaceStateStore(state_db_path)
    deliverables_config = DeliverablesConfig.from_env()

    handlers = HttpSurfaceHandlers(
        runtime=AgentRuntime(),
        message_bus=AgentMessageBroker(),
        tasks=state_store,
        event_store=state_store,
        deliverables_config=deliverables_config,
    )
    return RuntimeContainer(
        http_handlers=handlers,
        policy_engine=ActionPolicyEngine(),
        simulation_store=SimulationStateStore(),
    )


def create_app(*, runtime: RuntimeContainer | None = None) -> Flask:
    app = Flask(__name__)
    container = runtime or wire_runtime_dependencies()
    app.extensions["runtime_container"] = container

    @app.post("/webhooks/whatsapp")
    def post_webhooks_whatsapp():
        return to_json_response(container.http_handlers.post_webhooks_whatsapp(request.get_json(silent=True) or {}))

    @app.post("/jobs/orchestrate")
    def post_jobs_orchestrate():
        return to_json_response(container.http_handlers.post_jobs_orchestrate(request.get_json(silent=True) or {}))

    @app.post("/jobs/daily-suggestion")
    def post_jobs_daily_suggestion():
        return to_json_response(container.http_handlers.post_jobs_daily_suggestion(request.get_json(silent=True) or {}))

    @app.get("/api/v1/tasks/<task_id>")
    def get_task(task_id: str):
        return to_json_response(container.http_handlers.get_api_v1_tasks_id(task_id))


    @app.get("/simulator")
    def get_simulator_ui():
        return Response(SIMULATION_UI_HTML, mimetype="text/html")

    @app.get("/simulator/api/state")
    def get_simulator_state():
        return jsonify(container.simulation_store.snapshot())

    @app.post("/simulator/api/reset")
    def post_simulator_reset():
        return jsonify(container.simulation_store.reset())

    @app.post("/simulator/api/whatsapp-init")
    def post_simulator_whatsapp_init():
        payload = request.get_json(silent=True) or {}
        state = container.simulation_store.initialize_task(
            phone=str(payload.get("phone") or "unknown"),
            message=str(payload.get("message") or ""),
        )
        if bool(payload.get("auto_process")):
            try:
                state = container.simulation_store.apply_agent_simulation(
                    use_groq=bool(payload.get("use_groq", True)),
                    require_groq=bool(payload.get("require_groq", True)),
                    use_real_groq_api=bool(payload.get("use_real_groq_api", True)),
                )
                orchestration_response = container.http_handlers.post_jobs_orchestrate(
                    {
                        "task_id": str(state.get("task_id") or ""),
                        "prompt": str(state.get("prompt") or ""),
                        "deliverable": str(state.get("agent", {}).get("events", [{}])[-2].get("detail") or ""),
                        "subtasks": [
                            {"task_id": "sim-worker-1", "objective": "Compile response artifacts", "dependencies": []},
                            {"task_id": "sim-worker-2", "objective": "Validate response payload", "dependencies": ["sim-worker-1"]},
                        ],
                    }
                )
                state = container.simulation_store.apply_orchestration_result(orchestration=orchestration_response)
            except ValueError:
                state = container.simulation_store.snapshot()
        return jsonify(state)

    @app.post("/simulator/api/agent-run")
    def post_simulator_agent_run():
        payload = request.get_json(silent=True) or {}
        try:
            state = container.simulation_store.apply_agent_simulation(
                use_groq=bool(payload.get("use_groq", True)),
                require_groq=bool(payload.get("require_groq", False)),
                use_real_groq_api=bool(payload.get("use_real_groq_api", True)),
            )
        except ValueError:
            return jsonify(container.simulation_store.snapshot()), 400
        return jsonify(state)


    @app.post("/simulator/api/auto-run")
    def post_simulator_auto_run():
        payload = request.get_json(silent=True) or {}
        try:
            state = container.simulation_store.apply_agent_simulation(
                use_groq=bool(payload.get("use_groq", True)),
                require_groq=bool(payload.get("require_groq", True)),
                use_real_groq_api=bool(payload.get("use_real_groq_api", True)),
            )
            orchestration_response = container.http_handlers.post_jobs_orchestrate(
                {
                    "task_id": str(state.get("task_id") or ""),
                    "prompt": str(state.get("prompt") or ""),
                    "deliverable": str(state.get("agent", {}).get("events", [{}])[-2].get("detail") or ""),
                    "subtasks": [
                        {"task_id": "sim-worker-1", "objective": "Compile response artifacts", "dependencies": []},
                        {"task_id": "sim-worker-2", "objective": "Validate response payload", "dependencies": ["sim-worker-1"]},
                    ],
                }
            )
            state = container.simulation_store.apply_orchestration_result(orchestration=orchestration_response)
        except ValueError:
            return jsonify(container.simulation_store.snapshot()), 400
        return jsonify(state)

    @app.post("/simulator/api/approve")
    def post_simulator_approve():
        payload = request.get_json(silent=True) or {}
        try:
            state = container.simulation_store.apply_approval(
                approved=bool(payload.get("approved")),
                note=str(payload.get("note") or ""),
            )
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify(state)

    @app.post("/simulator/api/publish")
    def post_simulator_publish():
        try:
            state = container.simulation_store.publish_return()
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify(state)

    @app.post("/api/v1/approvals/<approval_id>/decision")
    def post_approval(approval_id: str):
        response = post_approval_decision(
            approval_id=approval_id,
            payload=request.get_json(silent=True) or {},
            authenticated_actor=_build_actor_context(),
            policy_engine=container.policy_engine,
        )
        return to_json_response(response)

    return app


app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8000")))

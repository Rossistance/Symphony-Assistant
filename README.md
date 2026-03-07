# Symphony Assistant

A Flask-based orchestration service for multi-agent task execution, messaging ingestion (WhatsApp-first), model-provider routing with failover, approval workflows for high-risk actions, and deliverable publishing (in-memory or Google Drive-like metadata contracts).

---

## What this repository contains

Symphony Assistant is structured around a set of composable runtime services:

- **HTTP surfaces** for webhook ingestion, orchestration jobs, daily suggestion jobs, task lookup, and approval decisions.
- **Agent runtime** that models lead/child delegation, staged execution plans (sequential/parallel/hybrid), and runtime event emission.
- **Messaging pipeline** with normalization + dedupe logic for inbound events and adapter-based channel routing.
- **Model router** with provider precedence, capability-aware selection, retries, and fallback transitions.
- **Policy engine** that classifies risk and enforces execution policies (auto, auto+audit, explicit approval).
- **Deliverable publishing** contracts that return consistent artifact metadata, with in-memory and Google Drive-backed variants.

The codebase is test-heavy and designed to keep interfaces stable while implementations evolve.

---

## Runtime API contract

The Flask app exposes these routes:

- `POST /webhooks/whatsapp`
  - Accepts normalized WhatsApp webhook payloads.
  - Performs validation + dedupe and emits ingestion/reply events.
- `POST /jobs/orchestrate`
  - Submits a task prompt and optional subtasks.
  - Runs orchestration/delegation flow and returns deliverable metadata.
- `POST /jobs/daily-suggestion`
  - Returns daily suggestion payload from the same HTTP surface handler set.
- `GET /api/v1/tasks/<task_id>`
  - Returns persisted task state.
- `POST /api/v1/approvals/<approval_id>/decision`
  - Applies approval decisions using trusted actor context from headers.

### Auth context for approval decision route

For `POST /api/v1/approvals/<approval_id>/decision`, the runtime expects auth context via headers:

- `X-Actor-Id`
- `X-Actor-Permissions` (comma-delimited, e.g. `approvals:decide`)

The server constructs an authenticated actor context from these headers and does **not** trust payload-level auth claims.

---

## Architecture overview

### 1. HTTP layer (`app/main.py`, `app/api/http_surfaces.py`)

- `create_app()` wires framework routes to pure handler methods.
- `HttpSurfaceHandlers` contains framework-agnostic orchestration logic and event emission.
- Task/event persistence uses `SqliteHttpSurfaceStateStore` by default via `HTTP_SURFACE_STATE_DB_PATH`.

### 2. Messaging layer (`app/messaging/*`, `app/gateway/*`, `app/webhooks/*`)

- Inbound webhook payloads are parsed into a normalized `InboundMessage`.
- `InboundIngestionPipeline` assigns dedupe keys and prevents duplicate processing.
- `AgentMessageBroker` coordinates inter-agent deliverable/message exchange events.
- Channel adapters currently include WhatsApp, SMS, and iOS bridge contracts.

### 3. Agent runtime and orchestration (`app/services/agent_runtime.py`, `app/services/orchestration_policy.py`)

- Supports registering a lead agent and spawning child agents under guardrails.
- Computes task-analysis inputs and chooses delegation mode based on urgency, dependency density, latency profile, and decomposition count.
- Persists execution-mode decisions for replay/inspection.

### 4. Model routing (`app/models/*`, `app/services/model_routing.py`)

- `ModelRouter` discovers provider adapters, filters by capability, and routes calls according to ordered precedence.
- Supports transient-error retries and provider fallback telemetry.
- Includes a backward-compatible legacy wrapper (`RoutedModelClient`) with deprecation notice.

### 5. Safety/policy layer (`app/services/policy_engine.py`, `app/api/approvals.py`)

- Risk levels map action types to policy:
  - low: auto execute
  - medium: auto execute + audit
  - high/unknown: explicit approval required
- Approval records and audit trail are maintained in memory by default.
- Approval API validates transitions and actor permissions.

### 6. Deliverables (`app/services/deliverables.py`, `app/config.py`)

- Uniform artifact metadata model (`artifact_id`, share URL, permission details, visibility, expiry metadata).
- `in_memory` backend for local/dev contracts.
- `google_drive` backend contract with folder policy, credential source validation, and visibility/expiry policy controls.

---

## Configuration

Configuration is environment-variable driven and centralized in `app/config.py`.

### Core messaging + model settings

- `DEFAULT_CHANNEL` (default `whatsapp`)
- `ENABLE_SMS_FALLBACK` (default `true`)
- `ENABLE_IOS_BRIDGE` (default `false`)
- `MODEL_ROUTER_ORDER` (default `groq,openai,gemini,anthropic`)
- `MODEL_PROVIDER_MAX_RETRIES` (default `2`)
- `SLOW_MODE_TRIGGER_PHRASES` (pipe-delimited)

### Deliverables settings

- `DELIVERABLES_BACKEND` (`in_memory` or `google_drive`)
- `DELIVERABLES_IN_MEMORY_DRIVE_ROOT`
- `GOOGLE_DRIVE_FOLDER_ID`
- `GOOGLE_DRIVE_CREDENTIALS_JSON` or `GOOGLE_DRIVE_CREDENTIALS_PATH`
- `GOOGLE_DRIVE_DELEGATED_SUBJECT`
- `GOOGLE_DRIVE_USE_SHARED_DRIVE`
- `GOOGLE_DRIVE_DRIVE_ID`
- `GOOGLE_DRIVE_SHARE_VISIBILITY` (`private` / `view_only`)
- `GOOGLE_DRIVE_SHARE_EXPIRY_HOURS`
- `GOOGLE_DRIVE_SUPPORTS_PERMISSION_EXPIRY`
- `GOOGLE_DRIVE_ALLOW_PARENT_OVERRIDE`
- `GOOGLE_DRIVE_ALLOWED_PARENT_IDS`
- `DELIVERABLES_ENVIRONMENT` (or `APP_ENV` fallback)

### Runtime persistence

- `HTTP_SURFACE_STATE_DB_PATH` (default `runtime/http_surface_state.db`)
- `MESSAGING_STATE_DB_PATH` (used by messaging runtime state store in tests/runtime)

---

## Local development quickstart

### 1) Create environment and install dependencies

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip pytest flask
```

### 2) Optional: generate `.env` with built-in local form

```bash
python3 -m http.server 8765
```

Open `http://localhost:8765/tools/env_builder.html`, generate env content, and paste into `.env`.

### 3) Run tests

```bash
pytest -q
```

### 4) Start the Flask app

```bash
python app/main.py
```

The app binds to `0.0.0.0:${PORT:-8000}`.

---

## Example request payloads

### Orchestrate task

```json
{
  "task_id": "task-123",
  "prompt": "Draft release notes for v1.2",
  "deliverable": "Release notes draft",
  "subtasks": [
    "Summarize customer-visible changes",
    "Highlight known issues"
  ]
}
```

### Approval decision

Headers:

- `X-Actor-Id: ops-admin`
- `X-Actor-Permissions: approvals:decide`

Body:

```json
{
  "decision": "approved"
}
```

---

## Testing strategy in this repo

The test suite validates:

- HTTP contract routes and response payload shapes.
- Runtime entrypoint wiring and Flask route behavior.
- Inbound dedupe and state-store semantics.
- Messaging adapters/gateway behavior.
- Policy engine evaluation + approval decision enforcement.
- Delegation/orchestration mode selection rules.
- Deliverables publisher configuration, metadata shape, and failure handling.
- Model router provider ordering, retry/fallback behavior, and telemetry.

Current repository suite size: **105 tests**.

---

## Documents included in this repository

- `docs/WEB_UI_SETUP.md` — practical setup flow for quick web interaction + environment form.
- `tools/env_builder.html` — no-code local UI to generate `.env` content.
- `Symphony 1.md`, `Symphony 2.md`, `Symphony 3.md`, `System Design Resource.md`, and `Claude Multi-Agent Framework.txt` — project planning/context artifacts.

---

## Work completed in this iteration

To create this README, I completed the following repository-evaluation tasks:

1. Inspected application structure and runtime entrypoint wiring.
2. Reviewed API handler and policy engine behavior.
3. Reviewed model routing and deliverables components.
4. Reviewed docs/setup assets.
5. Executed full test suite validation (`pytest -q`) after installing missing runtime dependency (`flask`).


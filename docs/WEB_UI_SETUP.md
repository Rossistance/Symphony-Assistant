# Symphony Assistant — Web UI + End-to-End Setup Guide

This guide gives you two practical options:

1. **No-code configuration UI now** (local HTML form that generates a `.env` block you can paste).
2. **API-driven web interaction now** (run the app handlers via tests or your own thin HTTP wrapper).

The project already defines key routes and orchestration behavior (WhatsApp webhook, orchestration jobs, daily suggestions, and task lookup), so you can wire a real web app quickly against these contracts.

---

## 1) Quickest path: use the built-in env UI form

A lightweight local UI has been added at:

- `tools/env_builder.html`

It generates environment variables aligned to this codebase defaults, including:

- `DEFAULT_CHANNEL`
- `ENABLE_SMS_FALLBACK`
- `ENABLE_IOS_BRIDGE`
- `MODEL_ROUTER_ORDER`
- `MODEL_PROVIDER_MAX_RETRIES`
- `SLOW_MODE_TRIGGER_PHRASES`
- API keys (optional inputs)

### Run it locally

From repo root:

```bash
python3 -m http.server 8765
```

Open:

- `http://localhost:8765/tools/env_builder.html`

Then:

1. Fill values.
2. Click **Generate**.
3. Click **Copy to Clipboard**.
4. Paste into a new `.env` file at repo root.

---

## 2) What you need to set up (checklist)

### A) Runtime prerequisites

- Python 3.10+
- `pip`
- virtual environment tool (`python -m venv`)

### B) Messaging provider setup

For production-first path (recommended):

- WhatsApp Cloud API app/account
- webhook endpoint exposed publicly (via cloud deployment or local tunnel)
- phone number + token configured in your actual integration layer

Optional fallback:

- SMS provider credentials if you want SMS failover enabled.

### C) Model provider setup

Minimum:

- `GROQ_API_KEY`

Recommended fallback keys:

- `OPENAI_API_KEY`
- `GEMINI_API_KEY`
- `ANTHROPIC_API_KEY`

### D) Deliverable storage setup

- Google Drive API credentials/service account (or whichever Drive-backed implementation you use)
- folder policy for generated artifacts
- sharing policy (private/view-only/time bound if implemented in your layer)

### E) Observability

- event sink/logging destination for route/mode/spawn/message/deliverable events
- correlation IDs (`task_id`, `run_id`, `agent_id`) in logs

---

## 3) Step-by-step bring-up right now

### Step 1 — Create a virtual env and install test deps

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -U pip pytest
```

### Step 2 — Generate your `.env`

Use `tools/env_builder.html` and paste output into `.env`.

At minimum, set:

- `DEFAULT_CHANNEL=whatsapp`
- `MODEL_ROUTER_ORDER=groq,openai,gemini,anthropic`
- `GROQ_API_KEY=...`

### Step 3 — Validate behavior with tests

```bash
pytest -q
```

Core tests to watch:

- messaging defaults and routing behavior
- model routing precedence/fallback
- approval policy behavior
- orchestration/mode selection
- acceptance HTTP surface handlers

### Step 4 — Run a minimal interactive UI layer

You have two fast options:

#### Option A: Postman/Insomnia as UI

Use these endpoints as contracts in your collection:

- `POST /webhooks/whatsapp`
- `POST /jobs/orchestrate`
- `POST /jobs/daily-suggestion`
- `GET /api/v1/tasks/:id`
- `POST /api/v1/approvals/:id/decision`
  - Route integration note: HTTP middleware must pass trusted auth context (subject/authenticated/permissions) to the handler separately from JSON payload; do not trust a payload `auth` object.

This is often the quickest way to “operate via web UI” immediately.

#### Option B: Build a thin web frontend (next step)

Create a small frontend (React/Next.js or plain HTML) with:

- task prompt form -> calls `/jobs/orchestrate`
- task detail panel -> calls `/api/v1/tasks/:id`
- approvals panel -> calls `/api/v1/approvals/:id/decision`
- webhook event log viewer (optional)

Start by wiring JSON forms directly to endpoint payloads; polish UX later.

### Step 5 — Wire production integrations incrementally

1. Connect real WhatsApp webhook transport.
2. Connect real Drive artifact publishing service.
3. Connect real model provider credentials and healthchecks.
4. Add durable DB/event storage (replace in-memory where needed).

### Step 6 — Verify acceptance scenarios manually

Run through:

1. Deliverable return (Drive link in channel reply).
2. Groq precedence with multiple keys configured.
3. Forced Groq timeout fallback.
4. Lead spawning workers + worker message exchange.
5. Parallel vs sequential mode choices.
6. Slow-mode phrase path (`this is a hard problem`).

---

## 4) Architecture facts from this repo you can rely on

- Routes/constants already defined for webhook/jobs/task lookup.
- Config defaults are WhatsApp-first + Groq-first router order.
- Slow-mode trigger phrases are configurable and already include variants of “let’s take it slow”.

That means the easiest implementation path is:

1. Keep these interfaces,
2. add real adapters/storage behind them,
3. expose them through your preferred web UI.

---

## 5) Recommended “easy UI” strategy

If you want a practical progression with minimal risk:

1. **Today:** use `tools/env_builder.html` + Postman collection as your interface.
2. **This week:** add a tiny internal dashboard (task submit + task status + approvals).
3. **Next:** add observability panels and artifact link previews.

This gives immediate usability without blocking on full product UI design.

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


## 6) New local workflow simulator UI (5 interactive windows)

A lightweight browser-based simulator is now available for local testing of the intended flow:

- WhatsApp initiation
- Agent/sub-agent task execution updates
- Approval decision
- Simulated Drive folder/file placement
- WhatsApp return reply

Run the Flask app:

```bash
python -m app.main
```

Open:

- `http://localhost:8000/simulator`

Notes:

- Simulator state is mirrored to a temp JSON file and also cached in browser `sessionStorage`.
- Browser close/reset calls clear simulator state.
- The simulator now supports automatic end-to-end flow from WhatsApp initiation through agent, orchestration, approval, drive publish, and WhatsApp return via the `Auto-run full workflow` control.
- When `Use real Groq API call` is enabled, the agent stage calls the live Groq Chat Completions endpoint using `GROQ_API_KEY` before continuing through orchestration.
- The agent pane can be configured to require `GROQ_API_KEY` and fail fast if missing, so you can verify a real key-backed model step in the chain.


## 7) One-shot install + run command (recommended)

From repo root, run:

```bash
bash scripts/run_local_simulator.sh
```

What this does:

1. Creates `.venv` (if missing)
2. Installs/updates `pip`
3. Installs `flask` and `pytest`
4. Starts the app and serves simulator UI at `http://localhost:8000/simulator`

Optional with Groq + custom port:

```bash
GROQ_API_KEY="your_real_groq_key" PORT=8000 bash scripts/run_local_simulator.sh
```

If you prefer manual commands instead of the script:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
python -m pip install flask pytest
python -m app.main
```

## 8) Windows PowerShell (no WSL) — exact commands

If you are running from **Windows PowerShell**, use the PowerShell script (not the bash script):

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_local_simulator.ps1
```

Optional with Groq + custom port:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_local_simulator.ps1 -Port 8000 -GroqApiKey "your_real_groq_key"
```

Manual PowerShell commands (if you prefer no script):

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m ensurepip --upgrade
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install flask pytest
$env:PORT="8000"
$env:GROQ_API_KEY="your_real_groq_key"   # optional
.\.venv\Scripts\python.exe -m app.main
```

### Common Windows errors and fixes

- `source: The term 'source' is not recognized...`
  - `source` is a bash command. In PowerShell, either run `.\.venv\Scripts\Activate.ps1` or call `.\.venv\Scripts\python.exe` directly.
- `No module named pip`
  - Use `py -m venv .venv`, then run `.\.venv\Scripts\python.exe -m ensurepip --upgrade` before pip install.
- `wsl: Failed to translate ...`
  - You are invoking a Linux/bash flow from Windows paths. Use `run_local_simulator.ps1` for native Windows execution.
- `No module named 'flask'`
  - Install with `.\.venv\Scripts\python.exe -m pip install flask pytest` and run app with the same interpreter.

## 9) Automatic real Grok orchestration and local artifact outputs

Normal simulator path is now automatic end-to-end from intake to completion:

1. Simulated WhatsApp intake (`/simulator/api/whatsapp-init`)
2. Real Grok API call (`https://api.x.ai/v1/chat/completions`)
3. Module-to-module handoff and synthesis
4. Final deliverable persisted locally
5. Simulated outbound WhatsApp completion reply

Required env for real model execution:

```bash
export GROK_API_KEY="your_xai_key"   # or XAI_API_KEY
export GROK_MODEL="grok-2-latest"    # optional override
```

Output location (default):

- `outputs/<task_id>/normalized_intake_payload.json`
- `outputs/<task_id>/grok_request_summary.json`
- `outputs/<task_id>/grok_response.json`
- `outputs/<task_id>/module_planning_output.json`
- `outputs/<task_id>/final_deliverable.txt`
- `outputs/<task_id>/execution_manifest.json`

Verification command:

```bash
python scripts/verify_auto_orchestration.py
```

The verifier fails fast if the real Grok key is missing, and checks that the expected output artifacts were written locally.

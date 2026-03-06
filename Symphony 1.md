*Symphony AI*

Below is a build-from-scratch blueprint for a **standalone multi-agent “Orchestrator Assistant”** that (1) does deep research with parallel agents, (2) proactively texts you, (3) executes real-world actions via tools you approve, and (4) shares a **common knowledge bus** (semantic memory) where agents persist artifacts (notes, code, URLs) and other agents discover + build on them.

I’m using the “Claude multi-agent research system” PDF as the guiding pattern (orchestrator/worker, parallel subagents, memory, citation pass, checkpointing, evals).  
I’ll also borrow a few general distributed-systems practices (retry, backoff/jitter, etc.) from your system design guide. 

---

## 1) What you’re building (three planes)

### A. Messaging plane (how it “talks to you”)

* **Inbound SMS → your app** (Twilio webhook hits your `/sms/inbound` endpoint). ([Twilio][1])
* **Outbound SMS ← your app** (your app calls Twilio “Message resource” to text you). ([Twilio][2])
* **Webhook security**: validate Twilio signatures. ([Twilio][3])

### B. Agent plane (multi-agent runtime)

A **lead/orchestrator** agent delegates to parallel **worker** agents (researchers, planners, coders, schedulers), then a **citation/verifier** agent checks evidence, and an **action executor** runs tools. This mirrors the orchestrator–worker pattern and “CitationAgent” pass in the Claude design.  

Key behaviors to copy directly:

* Explicit delegation instructions & boundaries for subagents 
* Scale effort (#agents/#tool calls) to query complexity 
* Parallelization at two levels: multiple subagents + subagent parallel tool calls 
* Long-horizon memory + “artifact outputs to filesystem” to avoid “telephone” 
* Checkpointing + resume after errors 

### C. Knowledge plane (shared semantic memory on “Google Drive as the database”)

Your “common knowledge bus” is:

* **Append-only event log** (everything any agent writes is an event)
* **Artifact store** (notes, code, URLs, plans, tool outputs)
* **Vector index** (embeddings + metadata) for semantic retrieval

Stored entirely in **Google Drive** using Drive API uploads. ([Google for Developers][4])
Optionally: Drive push notifications so the system can react to new artifacts instead of polling. ([Google for Developers][5])

---

## 2) Hard constraint you set (“no existing agent frameworks”)

You asked not to use prebuilt multi-agent frameworks. So the design below:

* **Does not rely on LangChain / AutoGen / CrewAI / etc.**
* Implements orchestration, tool calling, memory, routing, and eval harness as your own code.
* Still uses unavoidable external APIs (Twilio, Drive, model providers). Otherwise it can’t text you, store on Drive, or call LLMs.

---

## 3) Repo layout (start here)

```
orchestrator_assistant/
  app/
    main.py                  # HTTP server (webhooks + admin endpoints)
    config.py
    crypto.py                # signature validation, HMAC helpers
    http_client.py           # minimal HTTPS client wrapper w/ retries
    scheduler.py             # daily triggers + background jobs endpoints

    models/
      base.py                # ModelProvider interface
      openai_provider.py
      anthropic_provider.py
      gemini_provider.py
      router.py              # chooses best provider/model per task

    agents/
      runtime.py             # agent loop, tool loop, checkpointing
      orchestrator.py
      researcher.py
      worker.py
      citer.py               # citation/evidence pass
      planner.py             # daily-life planning
      executor.py            # runs real-world actions (tools)
      critic.py              # quality + safety gate (optional)

    tools/
      base.py                # Tool interface + schemas
      web_search.py          # search API wrapper (you choose provider)
      web_fetch.py           # fetch + extract text
      drive_store.py         # Drive-backed KB + state store
      sms_twilio.py          # inbound/outbound messaging
      google_calendar.py     # optional: calendar read/write
      gmail_send.py          # optional: email send
      todos.py               # your todo source adapter(s)

    memory/
      schemas.py             # Artifact/Event/State schemas
      semantic.py            # embeddings + cosine similarity search
      bus.py                 # append events, subscribe/poll

  Dockerfile
  README.md
```

---

## 4) Data schemas (the “knowledge bus” contract)

### 4.1 Artifact JSON (stored as a Drive file)

Each agent writes artifacts as immutable objects:

```json
{
  "artifact_id": "uuid",
  "type": "note|code|url|plan|tool_result|summary",
  "title": "string",
  "created_at": "ISO-8601",
  "created_by": "agent_name",
  "tags": ["research", "health", "errand"],
  "source": {
    "kind": "user|web|tool",
    "ref": "url-or-tool-run-id"
  },
  "content": {
    "text": "...",
    "language": "python",
    "code": "..."
  },
  "embeddings": {
    "provider": "openai|gemini|anthropic",
    "model": "text-embedding-3-large",
    "vector": [0.0123, -0.98, ...]
  },
  "links": ["artifact_id_2", "artifact_id_3"]
}
```

### 4.2 Event log JSONL (append-only)

Every write becomes an event in `kb/events.jsonl`:

```json
{"ts":"...","event":"ARTIFACT_WRITTEN","artifact_id":"...","by":"worker_2","run_id":"..."}
{"ts":"...","event":"TOOL_CALLED","tool":"calendar.create","by":"executor","run_id":"..."}
{"ts":"...","event":"SMS_SENT","to":"+1...","msg_id":"...","run_id":"..."}
```

This is your “bus”: agents discover new knowledge by reading events since last cursor.

### 4.3 Checkpoint state JSON (resumability)

Claude’s production lesson: agents are stateful, errors compound, and you need checkpoints + resume. 
Store after **every tool call** and after **every model response**:

```json
{
  "run_id":"uuid",
  "agent":"research_lead",
  "step": 17,
  "conversation": [...],
  "open_tasks": [...],
  "tool_history": [...],
  "memory_cursor": "byte_offset_or_event_id",
  "created_at":"..."
}
```

---

## 5) Storage: using Google Drive as the database

### 5.1 OAuth (web server flow)

Use Google’s OAuth 2.0 web-server flow to obtain refresh tokens and access Drive on your behalf. ([Google for Developers][6])

### 5.2 Drive file strategy (simple + robust)

Create a folder structure in Drive:

```
/OrchestratorDB/
  kb/
    artifacts/
    events.jsonl
    vectors.jsonl
  state/
    runs/<run_id>/<agent>.json
  secrets/
    oauth_tokens.json   (encrypted)
```

Upload/update files using Drive “files.create” and upload types. ([Google for Developers][4])

### 5.3 Reactivity

Option A (simpler): poll Drive every N seconds/minutes.
Option B (better): Drive push notifications (“watch”) to your webhook. ([Google for Developers][5])

---

## 6) Messaging (Twilio SMS)

### 6.1 Inbound webhook

Configure your Twilio number to hit:

* `POST https://<cloud-run-url>/sms/inbound` on inbound message ([Twilio][1])

### 6.2 Outbound SMS

Send via Twilio Message resource (HTTP POST). ([Twilio][2])

### 6.3 Verify requests

Validate `X-Twilio-Signature` for webhook security. ([Twilio][3])

---

## 7) Multi-provider models (your own routing layer)

You want “multiple models from multiple providers” and the system chooses by strength. Implement:

### 7.1 Provider interfaces

`ModelProvider` must support:

* `generate(messages, tools=None, json_schema=None, ...)`
* `embed(texts)`

Docs you’ll likely use:

* OpenAI API reference + embeddings. ([OpenAI Platform][7])
* OpenAI function calling / structured outputs. ([OpenAI Developers][8])
* Anthropic Messages API + tool use. ([Claude][9])
* Gemini generateContent + embeddings. ([Google AI for Developers][10])

### 7.2 A practical routing policy

A dead-simple first router (you’ll evolve it):

* **Research planning / synthesis** → strongest long-context reasoning model
* **Parallel web skims / extraction** → cheaper fast model
* **Code generation** → model you find best at code
* **Embeddings** → cheapest good embedding model (OpenAI or Gemini)
* **Judging / eval** → separate “judge model” (LLM-as-judge) per Claude’s eval approach 

Keep it deterministic at first (rules), then log outcomes and learn weights later.

---

## 8) Agent runtime (the core loop)

### 8.1 Orchestrator–worker (mirroring Claude)

Lead agent:

1. decomposes the task
2. writes the plan to memory (so it survives context limits) 
3. spawns subagents in parallel
4. synthesizes results
5. hands off to citation/verifier agent 

### 8.2 Scaling rules (copy these as prompt + code guardrails)

Claude found subagents can explode (e.g., “spawn 50 subagents”) and they added explicit effort budgets. 
Implement BOTH:

* **Prompt guidance** (lead agent instructed how many workers to use)
* **Hard limits** in code (max agents, max tool calls, max runtime)

Example policy (start here):

* simple fact: 1 agent, 3–10 tool calls
* comparison: 2–4 subagents, 10–15 calls each
* complex: 6–12 subagents, explicit divisions 

### 8.3 Parallelization

Two-level parallelization improves speed dramatically. 
Implement:

* Lead launches `asyncio.gather(...)` for subagents
* Subagents can run search + fetch + summarize concurrently

### 8.4 “Artifacts-first” subagent outputs

Claude’s appendix tip: have subagents write outputs directly to a filesystem and return references, to reduce “telephone.” 
In your system: subagent writes `artifact_id` and returns just that ID + 2–3 bullets.

---

## 9) Real-world actions (tools) with an approval gate

### 9.1 Tool contract

Every tool has:

* `name`
* `json_schema` for args
* `side_effect` boolean
* `execute(args) -> result`

### 9.2 Approval flow (strongly recommended)

For anything with side effects (sending email, booking, deleting files):

1. agent proposes an action plan (JSON)
2. system texts you: “Approve? Reply YES <code> or NO”
3. only then execute

This prevents “oops” actions and keeps you in control.

---

## 10) Daily-life assistant behaviors (proactive orchestration)

You said: “make suggestions on aspects of my daily life it has access to via supplied context of todos and things happening day to day.”

Implement two loops:

### 10.1 Context ingestion loop

On a schedule:

* Read todos (your source)
* Read calendar
* Read recent SMS conversation + open commitments
* Write a single “Daily Context Snapshot” artifact

### 10.2 Proactive suggestion loop

A “Planner” agent reads the snapshot and produces:

* top 3 priorities
* risk/conflict warnings (“you have overlap”)
* gentle nudges (“leave in 20 min”)
* optional optimizations (“batch errands near X”)

Then it **texts you** a concise briefing.

Schedule via Cloud Scheduler calling your Cloud Run endpoint. ([Google Cloud Documentation][11])

---

## 11) Deployment on Google Cloud (Cloud Run + Scheduler + Secrets)

### 11.1 Cloud Run

Containerize the app and deploy to Cloud Run. ([Google Cloud Documentation][12])

### 11.2 Cloud Scheduler

Trigger daily briefings / periodic memory compaction / Drive polling. ([Google Cloud Documentation][11])

### 11.3 Secrets

Store API keys (Twilio + model providers) in Secret Manager and mount them into Cloud Run. ([Google Cloud Documentation][13])

---

## 12) Reliability + observability (production-grade behaviors)

Claude’s big production points:

* errors compound, so you need checkpointing, retries, and graceful degradation 
* tracing and monitoring of agent decision patterns (without violating user privacy) 

### 12.1 Retry strategy

Use exponential backoff + jitter for transient failures (Drive, Twilio, model APIs). Your system design guide summarizes common retry approaches. 

### 12.2 Checkpoints

Persist:

* after tool call
* after model call
* after each “phase” (plan → research → draft → cite → action)

### 12.3 Deployment safety

If you update frequently, do gradual rollouts (“rainbow deployments”) so in-flight runs aren’t broken. 

---

## 13) Evaluation (don’t skip this)

Claude’s evaluation approach that you should copy:

* start with a small set (~20) realistic tasks early 
* use LLM-as-judge with a rubric: factual accuracy, citation accuracy, completeness, source quality, tool efficiency 
* keep human spot checks because automated eval misses weird failure modes 

---

# Minimal “from-scratch” code skeleton (key parts)

Below are short, foundational pieces you’ll expand. They avoid agent frameworks and keep dependencies to a minimum (stdlib HTTP + JSON). Replace placeholders with your keys/secrets.

## A) A tiny HTTP server for Twilio + scheduler webhooks

```python
# app/main.py
import json
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs

from app.tools.sms_twilio import twilio_parse_inbound
from app.agents.orchestrator import handle_user_message
from app.scheduler import handle_scheduled_job

class Handler(BaseHTTPRequestHandler):
    def _read_body(self) -> bytes:
        length = int(self.headers.get("Content-Length", "0"))
        return self.rfile.read(length)

    def _send(self, code: int, body: str, content_type="text/plain"):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))

    def do_POST(self):
        if self.path == "/sms/inbound":
            raw = self._read_body().decode("utf-8")
            # Twilio sends x-www-form-urlencoded by default
            form = parse_qs(raw)
            inbound = twilio_parse_inbound(form, headers=self.headers)
            # Kick off orchestration synchronously for v1; evolve to async job queue later
            reply_text = handle_user_message(inbound["from"], inbound["body"])
            # Respond with TwiML (simple)
            twiml = f'<?xml version="1.0" encoding="UTF-8"?><Response><Message>{reply_text}</Message></Response>'
            return self._send(200, twiml, content_type="application/xml")

        if self.path.startswith("/jobs/"):
            body = self._read_body().decode("utf-8") or "{}"
            payload = json.loads(body)
            out = handle_scheduled_job(self.path, payload)
            return self._send(200, json.dumps(out), content_type="application/json")

        return self._send(404, "not found")

def main():
    # Cloud Run provides PORT
    import os
    port = int(os.environ.get("PORT", "8080"))
    srv = HTTPServer(("0.0.0.0", port), Handler)
    srv.serve_forever()

if __name__ == "__main__":
    main()
```

## B) Twilio inbound parsing + signature verification hook

```python
# app/tools/sms_twilio.py
import base64
import hashlib
import hmac

def verify_twilio_signature(auth_token: str, url: str, params: dict, signature: str) -> bool:
    # Twilio provides official libs, but you asked for “from scratch.”
    # Use docs to match their signing procedure.
    mac = hmac.new(auth_token.encode("utf-8"), digestmod=hashlib.sha1)
    mac.update(url.encode("utf-8"))
    for k in sorted(params.keys()):
        mac.update(k.encode("utf-8"))
        mac.update(params[k].encode("utf-8"))
    digest = base64.b64encode(mac.digest()).decode("utf-8")
    return hmac.compare_digest(digest, signature)

def twilio_parse_inbound(form: dict, headers) -> dict:
    # form values arrive as lists from parse_qs
    def g(key: str) -> str:
        v = form.get(key, [""])
        return v[0] if v else ""

    return {
        "from": g("From"),
        "to": g("To"),
        "body": g("Body"),
        "message_sid": g("MessageSid"),
        "signature": headers.get("X-Twilio-Signature", "")
    }
```

Twilio’s webhook flow and signature validation requirements are documented here. ([Twilio][1])

## C) Provider-agnostic model interface + router

```python
# app/models/base.py
from dataclasses import dataclass
from typing import Any, Optional

@dataclass
class ModelResult:
    text: str
    raw: Any

class ModelProvider:
    name: str

    def generate(self, *, messages: list[dict], tools: Optional[list[dict]] = None,
                 json_schema: Optional[dict] = None, temperature: float = 0.2) -> ModelResult:
        raise NotImplementedError

    def embed(self, texts: list[str]) -> list[list[float]]:
        raise NotImplementedError
```

```python
# app/models/router.py
from dataclasses import dataclass

@dataclass
class TaskHint:
    kind: str  # "research|summarize|code|judge|tool_planning|chat"
    budget: str  # "low|med|high"
    needs_tools: bool = False

class ModelRouter:
    def __init__(self, providers: dict):
        self.providers = providers

    def pick(self, hint: TaskHint):
        # v1: deterministic rules; evolve later.
        if hint.kind == "judge":
            return self.providers["openai"]  # example
        if hint.kind in ("research", "tool_planning") and hint.budget == "high":
            return self.providers["anthropic"]
        if hint.kind == "summarize":
            return self.providers["gemini"]
        return self.providers["openai"]
```

(You’ll map to your preferred models per provider.)

## D) Knowledge bus (append-only events + artifact writes)

```python
# app/memory/bus.py
import json
import time
import uuid

from app.tools.drive_store import DriveStore

class KnowledgeBus:
    def __init__(self, store: DriveStore):
        self.store = store

    def append_event(self, event: dict) -> None:
        line = json.dumps(event, ensure_ascii=False) + "\n"
        self.store.append_text("kb/events.jsonl", line)

    def write_artifact(self, artifact: dict) -> str:
        artifact_id = artifact.get("artifact_id") or str(uuid.uuid4())
        artifact["artifact_id"] = artifact_id
        artifact["created_at"] = artifact.get("created_at") or time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        path = f"kb/artifacts/{artifact_id}.json"
        self.store.put_json(path, artifact)
        self.append_event({"ts": artifact["created_at"], "event": "ARTIFACT_WRITTEN", "artifact_id": artifact_id, "by": artifact.get("created_by","unknown")})
        return artifact_id
```

## E) Orchestrator pattern (lead spawns workers)

```python
# app/agents/orchestrator.py
import uuid
from app.models.router import TaskHint
from app.agents.researcher import run_research_loop
from app.agents.planner import run_daily_planner
from app.tools.sms_twilio_outbound import send_sms
from app.tools.drive_store import drive_store_singleton
from app.memory.bus import KnowledgeBus

bus = KnowledgeBus(drive_store_singleton())

def handle_user_message(user_phone: str, text: str) -> str:
    run_id = str(uuid.uuid4())

    # 1) Persist inbound
    bus.append_event({"ts": now(), "event": "SMS_INBOUND", "from": user_phone, "text": text, "run_id": run_id})

    # 2) Decide: is this research? action? life planning?
    if text.lower().startswith("research:"):
        query = text.split(":",1)[1].strip()
        report = run_research_loop(run_id, query)
        return report

    if text.lower().startswith("plan my day"):
        brief = run_daily_planner(run_id, user_phone)
        return brief

    # default: quick assist (you’ll implement)
    return "Got it. Try 'Research: <topic>' or 'Plan my day'."

def now():
    import time
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
```

---

## 14) The research loop (close to the Claude design)

Implement it exactly like the PDF describes:

* lead writes a plan to memory before heavy work (context overflow insurance) 
* lead spawns specialized subagents with explicit boundaries 
* lead decides if more research is needed, then citation pass 

---

## 15) Cloud deployment checklist (Google Cloud + Drive)

1. **Create GCP project**
2. Enable APIs: Cloud Run, Secret Manager, Drive API
3. Implement OAuth web-server flow for Drive access ([Google for Developers][6])
4. Deploy container to Cloud Run ([Google Cloud Documentation][12])
5. Configure Twilio webhook to Cloud Run `/sms/inbound` ([Twilio][1])
6. Store secrets in Secret Manager, mount into Cloud Run ([Google Cloud Documentation][13])
7. Create Cloud Scheduler jobs to hit `/jobs/daily_briefing` etc. ([Google Cloud Documentation][11])
8. (Optional) Configure Drive push notifications to your webhook ([Google for Developers][5])

---

## 16) What to build first (fastest path to a “living” assistant)

1. **SMS in/out** (Twilio) + webhook security ([Twilio][2])
2. **Drive-backed event log + artifact store** ([Google for Developers][4])
3. **Single orchestrator agent** that can:

   * store plans/notes
   * run a “research loop” with 2 workers
4. Add **semantic memory** (embeddings + cosine) using OpenAI or Gemini embeddings ([OpenAI Developers][14])
5. Add **daily context snapshot → planner → proactive SMS** using Cloud Scheduler ([Google Cloud Documentation][11])
6. Add **approval-gated action tools** (calendar/email/etc.)

---

If you want, I can extend this into a **fully filled-in v1 implementation** (still “no agent frameworks”) by writing the remaining modules (Drive REST client, OpenAI/Anthropic/Gemini REST wrappers, a simple web search tool, research-worker prompts, citation pass, and the approval gate) in the same style as the skeleton above—so you can paste it into a repo and deploy.

[1]: https://www.twilio.com/docs/usage/webhooks/messaging-webhooks?utm_source=chatgpt.com "Messaging Webhooks"
[2]: https://www.twilio.com/docs/messaging/tutorials/how-to-send-sms-messages?utm_source=chatgpt.com "Send SMS and MMS messages"
[3]: https://www.twilio.com/docs/usage/webhooks/webhooks-security?utm_source=chatgpt.com "Webhooks security"
[4]: https://developers.google.com/workspace/drive/api/guides/manage-uploads?utm_source=chatgpt.com "Upload file data | Google Drive"
[5]: https://developers.google.com/workspace/drive/api/guides/push?utm_source=chatgpt.com "Notifications for resource changes | Google Drive"
[6]: https://developers.google.com/identity/protocols/oauth2/web-server?utm_source=chatgpt.com "Using OAuth 2.0 for Web Server Applications | Authorization"
[7]: https://platform.openai.com/docs/api-reference/?utm_source=chatgpt.com "API Reference - OpenAI API"
[8]: https://developers.openai.com/api/docs/guides/function-calling/?utm_source=chatgpt.com "Function calling | OpenAI API"
[9]: https://platform.claude.com/docs/en/build-with-claude/working-with-messages?utm_source=chatgpt.com "Using the Messages API - Claude Docs"
[10]: https://ai.google.dev/api/generate-content?utm_source=chatgpt.com "Generating content | Gemini API - Google AI for Developers"
[11]: https://docs.cloud.google.com/run/docs/triggering/using-scheduler?utm_source=chatgpt.com "Running services on a schedule"
[12]: https://docs.cloud.google.com/run/docs/deploying?utm_source=chatgpt.com "Deploying container images to Cloud Run"
[13]: https://docs.cloud.google.com/run/docs/configuring/services/secrets?utm_source=chatgpt.com "Configure secrets for services | Cloud Run"
[14]: https://developers.openai.com/api/reference/resources/embeddings/?utm_source=chatgpt.com "Embeddings | OpenAI API Reference"

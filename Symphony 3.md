# Symphony 3 — Updated Build Instructions (Messaging + Groq-First + Swarm Autonomy)

This document updates the prior plan with your latest strategy:

1. Replace SMS-first transport with **rich mobile messaging** that supports file/link sharing and persistent notifications.
2. Make **Groq Cloud the first/default model provider**, while keeping OpenAI/Gemini/Claude adapters for future experimentation.
3. Make the runtime a **full-auto multi-agent swarm** where agents can:
   - spawn additional agents,
   - message other agents,
   - and dynamically choose parallel vs sequential execution per task analysis.
4. Change approval and orchestration mode:
   - default mode: high-autonomy execution,
   - controlled mode for special user instructions like **"this is a hard problem"** or **"let's take it slow"** (context-preserving lead + delegated support).

---

## 1) Messaging strategy update

## 1.1 Decision

Adopt a **channel abstraction** and prioritize:

1. **WhatsApp Cloud API** as primary transport for production reliability and multimedia support.
2. **iOS Messenger bridge** as optional integration layer (where technically/legal feasible) for personal-device workflows.
3. Keep legacy SMS adapter but no longer default route.

Why:
- You need rich media, file links, and mobile persistent notifications.
- WhatsApp supports media + links + webhook lifecycle semantics suitable for agent loops.
- iMessage direct bot automation is constrained; treat iOS Messenger support as bridge-based and optional.

## 1.2 Required capability

When a task produces deliverables:
1. Save artifact(s) to Drive (canonical store).
2. Create share link(s) with policy (`private`, `view-only`, `time-bound` if supported).
3. Reply through the **same originating channel/thread** with:
   - short summary,
   - artifact links,
   - optional attachment previews.

### Delivery contract

```json
{
  "reply_to_message_id": "provider message id",
  "task_id": "uuid",
  "deliverables": [
    {
      "artifact_id": "uuid",
      "title": "Competitor Analysis",
      "drive_file_id": "...",
      "share_url": "https://drive.google.com/...",
      "mime_type": "application/pdf"
    }
  ],
  "status": "completed"
}
```

---

## 2) Groq-first model strategy (with multi-provider retention)

## 2.1 Router policy

Implement provider order:

1. **Groq first** (if key/config present and health is good).
2. OpenAI fallback.
3. Gemini fallback.
4. Claude fallback.

Do not remove non-Groq providers. Keep them pluggable and enabled by configuration.

## 2.2 Provider interface

Unify to a single interface:

- `generate(messages, tools, response_schema, temperature, max_tokens)`
- `embed(texts)`
- `healthcheck()`

At startup, discover providers from env/config and build a capability matrix:

```json
{
  "groq": {"chat": true, "tool_calling": true, "embeddings": false},
  "openai": {"chat": true, "tool_calling": true, "embeddings": true},
  "gemini": {"chat": true, "tool_calling": true, "embeddings": true},
  "anthropic": {"chat": true, "tool_calling": true, "embeddings": false}
}
```

## 2.3 Task-based route examples

- `classify`, `triage`, `short-suggest` → Groq fast model.
- `planning`, `multi-hop reasoning`, `synthesis` → Groq reasoning model; fallback chain if degraded.
- `embedding tasks` → provider with embedding support (OpenAI/Gemini) until Groq embeddings are available in your configured tier.

## 2.4 Call discipline from app

For every model call:
1. Build trace context (`run_id`, `agent_id`, `task_type`, `budget`).
2. Request via router (not direct provider calls).
3. If provider fails transiently, retry with jitter.
4. If retries exhausted, promote fallback provider.
5. Persist call metadata: tokens, latency, cost estimate, outcome.

---

## 3) Swarm runtime updates

## 3.1 Agents can spawn agents

Add `spawn_agent` as a first-class runtime primitive:

```ts
spawnAgent({
  parentAgentId,
  role,
  objective,
  constraints,
  maxToolCalls,
  maxRuntimeSec
}) -> childAgentId
```

Rules:
- inherit correlation and safety scope from parent,
- child gets explicit objective and output contract,
- parent remains accountable for synthesis.

## 3.2 Agents can message agents

Add internal mailbox/event bus:

- `send_agent_message(from, to, topic, payload_ref)`
- `subscribe_agent_messages(agent_id)`

Use this to avoid bloating lead-agent context; pass references to artifacts rather than large inline payloads.

## 3.3 Parallel vs sequential decision policy

At orchestration start, run **Task Analysis Pass**:

Inputs:
- decomposition count estimate,
- dependency graph density,
- urgency,
- expected token budget,
- tool latency profile.

Output mode:
- `SEQUENTIAL` when steps are dependent and require tight iteration.
- `PARALLEL` when branches are independent and high-latency.
- `HYBRID` when initial fan-out then sequential synthesis is optimal.

Record decision as event:

```json
{"event":"execution_mode_selected","mode":"hybrid","reason":"independent research branches + dependent final synthesis"}
```

---

## 4) Autonomy + approval flow update

## 4.1 Default mode: full-auto swarm

By default, the system should execute end-to-end without user coordination for normal tasks:
- triage,
- decomposition,
- subagent delegation,
- tool execution,
- deliverable generation,
- return results in channel.

## 4.2 Controlled mode trigger phrases

If user includes:
- "this is a hard problem"
- "lets take it slow" / "let's take it slow"

then for that task group:
1. Head agent becomes context guardian and minimizes context churn.
2. Delegates supporting/tangential work to specialists (research, review, perf-test).
3. Maintains a persistent canonical plan artifact and only ingests condensed sub-results.
4. Increases verification depth before action.

This creates a deliberate slow-and-careful mode without sacrificing swarm throughput in general operation.

## 4.3 Approval policy refinement

Keep approval requirements for **high-risk side effects** (financial transfer, external sharing to third parties, deletion/destructive operations), but avoid unnecessary user interrupts for low-risk internal steps.

Policy table:
- Low risk (research fetch, summarize, internal artifact generation): auto.
- Medium risk (sending message to user, adding Drive files in user-owned folder): auto + audit.
- High risk (external recipients, destructive edits, purchases, credential changes): explicit approval.

---

## 5) Drive deliverables + in-channel return flow

Implement workflow:

1. Worker/subagent writes deliverable artifact (`report.md`, `analysis.pdf`, `code.zip`) to Drive-backed store.
2. Executor sets sharing policy and creates per-file `share_url`.
3. Messaging adapter responds in original thread with structured summary and links.
4. Event log records `deliverable.published` and `message.reply.sent`.

Suggested message template:

```
Done — I completed <task title>.

Deliverables:
1) <name> — <share_url>
2) <name> — <share_url>

If you want, I can now run a critique pass and produce a shorter executive summary.
```

---


## 5.1 Deliverables publisher utility/service (implementation contract)

Create a dedicated service, e.g. `DeliverablePublisher`, responsible for taking generated artifacts and publishing them to Drive-backed storage.

### Service responsibilities

1. Accept one or more generated artifacts from workers/orchestrator (`name`, `local_path|bytes`, `mime_type`, `task_id`, optional metadata).
2. Upload/save each artifact into Drive-backed storage and persist internal mapping (`artifact_id -> drive_file_id`).
3. Apply share policy per file:
   - `private` (owner-only),
   - `view-only` (link-reader, no edit),
   - optional `expires_at` for time-bound access (if provider supports expiry).
4. Return canonical metadata for downstream messaging and audit:
   - `drive_file_id`,
   - `share_url`,
   - `mime_type`.

### Suggested interface

```ts
publishDeliverables({
  taskId,
  runId,
  correlationId,
  replyToMessageId,
  artifacts,
  sharePolicy
}) => {
  task_id,
  reply_to_message_id,
  deliverables: Array<{
    artifact_id,
    title,
    drive_file_id,
    share_url,
    mime_type
  }>,
  status
}
```

### Share policy schema

```json
{
  "visibility": "private | view-only",
  "expires_at": "2026-12-31T23:59:59Z"
}
```

`expires_at` is optional; when unsupported by the backend, record capability downgrade in event metadata and keep policy as close as possible.

## 5.2 Delivery payload contract (required)

Use this payload for orchestration -> messaging adapter handoff:

```json
{
  "reply_to_message_id": "provider-message-id",
  "task_id": "uuid",
  "deliverables": [
    {
      "artifact_id": "uuid",
      "title": "Competitor Analysis",
      "drive_file_id": "1AbC...",
      "share_url": "https://drive.google.com/...",
      "mime_type": "application/pdf"
    }
  ],
  "status": "completed"
}
```

Status should be constrained to: `completed | partial | failed`.

## 5.3 Messaging adapter thread reply wiring

Require the messaging adapter to reply back to the same thread/channel that initiated the task.

Routing keys that must be carried end-to-end:
- `channel` (e.g., whatsapp, ios_bridge, sms),
- `conversation_id`/`thread_id`,
- `reply_to_message_id`,
- `task_id`.

Outbound message should be concise:
1. One-line completion summary (`Done — completed <task title>.`).
2. Bullet/numbered link list from `deliverables[].share_url`.
3. Optional call-to-action line (`Reply "summarize" for executive summary.`).

## 5.4 Structured events for publish + dispatch

Emit structured events with task/run correlation IDs for observability and replay.

### `deliverable.published`

```json
{
  "event": "deliverable.published",
  "timestamp": "2026-03-06T21:00:00Z",
  "task_id": "uuid",
  "run_id": "uuid",
  "correlation_id": "uuid",
  "channel": "whatsapp",
  "artifact_id": "uuid",
  "drive_file_id": "1AbC...",
  "share_url": "https://drive.google.com/...",
  "mime_type": "application/pdf",
  "share_policy": {"visibility": "view-only", "expires_at": null}
}
```

### `message.reply.sent`

```json
{
  "event": "message.reply.sent",
  "timestamp": "2026-03-06T21:00:02Z",
  "task_id": "uuid",
  "run_id": "uuid",
  "correlation_id": "uuid",
  "channel": "whatsapp",
  "thread_id": "abc-thread",
  "reply_to_message_id": "wamid.HBg...",
  "message_id": "wamid.HBg...reply",
  "status": "completed",
  "deliverable_count": 2
}
```

## 6) API + config changes you should implement now

## 6.1 Config

Add env flags:

- `DEFAULT_CHANNEL=whatsapp`
- `ENABLE_SMS_FALLBACK=true`
- `ENABLE_IOS_BRIDGE=false`
- `GROQ_API_KEY=...`
- `MODEL_ROUTER_ORDER=groq,openai,gemini,anthropic`
- `SLOW_MODE_TRIGGER_PHRASES=this is a hard problem|let's take it slow|lets take it slow`

## 6.2 Runtime APIs

- `POST /webhooks/whatsapp`
- `POST /jobs/orchestrate`
- `POST /jobs/daily-suggestion`
- `POST /api/v1/approvals/:id/decision`
- `GET /api/v1/tasks/:id`

## 6.3 Event types

- `task.analysis.completed`
- `execution.mode.selected`
- `agent.spawned`
- `agent.message.sent`
- `deliverable.published`
- `message.reply.sent`
- `provider.route.selected`
- `provider.route.fallback`

---

## 7) Prompting policy for lead orchestrator

System prompt directives to include:

1. Determine execution mode (parallel/sequential/hybrid) before delegation.
2. Use Groq-first route unless unavailable/unhealthy.
3. Prefer artifact references over long inline outputs.
4. Spawn subagents with explicit boundaries and no duplicated objectives.
5. In slow/hard mode, preserve lead context and offload tangential work to support agents.
6. Return deliverables through the original user channel with Drive links.

---

## 8) Immediate implementation sequence (practical)

1. Switch default transport from SMS to WhatsApp adapter.
2. Add `reply-with-deliverables` utility (Drive link + thread reply).
3. Introduce Groq provider + router precedence.
4. Add `spawn_agent` and `agent_mailbox` primitives.
5. Add execution-mode selector (parallel/sequential/hybrid).
6. Add slow-mode trigger detector and context-preserving plan behavior.
7. Update approval policy by risk class.
8. Add evals for mode selection, route fallback, and deliverable-return success.

---

## 9) Acceptance tests (must pass)

1. **Deliverable return test**
   - Input: user asks for report via WhatsApp.
   - Expectation: report saved to Drive and reply contains working share link.

2. **Groq precedence test**
   - With Groq + OpenAI keys present, model call should route to Groq first.

3. **Fallback test**
   - Simulate Groq timeout; verify automatic fallback to next provider.

4. **Swarm spawn/message test**
   - Lead spawns 2 workers; workers exchange artifact refs via mailbox; synthesis succeeds.

5. **Mode selection test**
   - Independent research task -> parallel/hybrid.
   - Strict dependency task -> sequential.

6. **Slow mode test**
   - Message contains "this is a hard problem".
   - Expect deeper verification path and context-guardian lead behavior.

---

## 10) Final guidance

This updated approach keeps your long-term flexibility while matching your immediate priorities:
- richer mobile communication,
- Groq-first access to many models through one service,
- autonomous swarm behavior,
- and controlled high-discipline mode only when you request it.

Build it as your own first-party orchestration runtime with clear contracts, strict observability, and replay-safe state so it remains novel, maintainable, and production-grade.

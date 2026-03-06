I’m treating this as a build-ready MVP for a personal, standalone WhatsApp orchestrator assistant: inbound on WhatsApp, custom multi-agent orchestration, Groq model routing, shared semantic memory, Google Drive as the user-facing document store, approval-gated actions, and daily-context suggestions. I’m defaulting to a modular monolith for MVP because the systems-architect package explicitly favors the simplest credible path to ship, with monolith + Postgres + REST, adding queues only when justified, and avoiding premature microservices/Kafka/GraphQL complexity. The multi-agent shape follows the orchestrator-worker pattern from the research-system reference: a lead agent persists its plan to memory, delegates bounded tasks to subagents, and uses clear task boundaries, output contracts, and a final reflection/citation pass.

Product brief

Problem
You want a WhatsApp-native assistant that can receive requests, plan work, search/summarize across Google Drive docs, keep durable shared memory, ask for approval before risky actions, and proactively suggest useful next steps from daily context.

Audience
Primary: one power user / founder / exec.
Secondary later: small team with delegated approvals and shared Drive spaces.

Core workflows

Send a WhatsApp message like “Review today’s notes and suggest priorities.”

Assistant ingests recent Drive docs + saved context + reminders, then replies with suggestions.

Send a task like “Research competitors and draft a summary doc.”

Orchestrator decomposes into worker runs, stores findings in memory, writes output back to Drive, and replies in WhatsApp.

Ask for actions like “Email this summary to Sam” or “Create a reminder for tomorrow.”

Assistant requests approval for risky actions, executes only after explicit approval, and logs everything.

MVP promise
A reliable WhatsApp assistant that can plan, research, retrieve from Drive-backed knowledge, remember useful facts, ask for approval before actions, and send daily suggestions.

Success criteria

P95 inbound webhook ack under 1.5s

First useful response/update under 30s for normal tasks

Zero unapproved destructive/sensitive actions

Daily suggestions delivered by local-time schedule

Retrieval freshness under 10 minutes after Drive sync

Full audit trail for approvals and executed actions

Three high-impact assumptions

I’m proceeding with these defaults:

Meta WhatsApp Cloud API for inbound/outbound messaging.

GCP Cloud Run + Cloud SQL Postgres + Redis for staging/prod.

Single-user first, team-ready schema later.

MVP scope

Included

WhatsApp webhook ingest + outbound replies

Lead orchestrator + specialized workers

Groq model router with env-configured model aliases

Postgres + pgvector shared semantic memory

Google Drive sync, chunking, indexing, and report write-back

Approval requests/resolution via WhatsApp + admin console

Action adapters: WhatsApp send, email/webhook placeholder, reminder scheduling

Daily-context ingestion and proactive suggestion job

Audit log, event log, queue, retries, basic observability

Deferred

Multi-user shared workspaces with granular RBAC

Google Calendar/Gmail full adapters

Voice notes/transcription

Full citation UI

Fine-tuned reranking / hybrid search service

Separate vector DB / search cluster

Out of scope for MVP

Autonomous destructive actions without approval

Complex enterprise tenancy

Heavy real-time collaboration UI

Kubernetes / microservices split

Key design choices and tradeoffs

1) Modular monolith, not microservices
Why: fastest shippable path, lower ops burden, simpler ownership.
Tradeoff resolved: speed vs future isolation.
Prevents: premature distributed complexity.
Default: single deployable API + worker + admin, clean internal module boundaries.
Change later when: separate teams, distinct scaling envelopes, or very high job throughput appear.

2) Queue only where it matters
Why: webhooks, outbound messaging, Drive sync, research runs, and daily suggestion jobs are async and bursty.
Tradeoff resolved: responsiveness vs consistency/complexity.
Prevents: webhook timeouts and synchronous long-running work.
Default: Redis + BullMQ, idempotent jobs, DLQ-style failed queue.
Change later when: higher scale or cross-region streaming justifies Pub/Sub/Kafka. The KB explicitly says async work must come with idempotency, retries, backpressure handling, poison-message handling, and queue-lag observability.

3) Postgres + pgvector, not separate vector/search infra
Why: one source of truth for state, memory metadata, and embeddings.
Tradeoff resolved: simplicity vs peak retrieval scale.
Prevents: early dual-write/index drift between operational DB and separate vector store.
Default: relational state + pgvector for chunks/facts/memory lookup.
Change later when: corpus size, recall latency, or retrieval experimentation require a dedicated vector/search layer. This follows the package’s SQL-first default and complexity restraint.

4) Approval boundary is first-class
Why: action execution is the highest-risk surface.
Tradeoff resolved: autonomy vs safety.
Prevents: unauthorized outbound actions, double execution, permission leaks.
Default: all risky actions require approval token + TTL + scope + audit log.
Change later when: user explicitly whitelists low-risk actions or policy engine matures. The systems-architect package calls out dangerous actions protection, audit trail, security baseline, and risky-action protections as non-optional.

5) Deploy-ready from day one
Why: the package requires actual artifacts, deployment setup, QA, smoke tests, rollback, and observability.
Tradeoff resolved: faster demo vs fewer production surprises.
Prevents: “works locally, breaks in staging” handoff failures.

Architecture
User (WhatsApp)
   |
   v
Meta WhatsApp Cloud API
   |
   v
/apps/api ---------------> Postgres (state + memory metadata + pgvector)
   |                            |
   |                            +--> event_log / audit_log / approvals / tasks
   |
   +--> Redis/BullMQ ----------> /apps/worker
                                      |
                                      +--> Lead Orchestrator
                                      |      |
                                      |      +--> Agent Registry
                                      |      +--> Groq Router
                                      |      +--> Shared Memory Retrieval
                                      |      +--> Delegation Planner
                                      |
                                      +--> Worker Agents
                                      |      - research
                                      |      - retrieval
                                      |      - summarizer
                                      |      - reflection/citation
                                      |      - suggestion
                                      |
                                      +--> Action Executor
                                      |      - whatsapp send
                                      |      - webhook
                                      |      - email (placeholder)
                                      |      - scheduler/reminder
                                      |
                                      +--> Google Drive Sync/Writer
                                      |
                                      +--> Approval Service
   |
   +--> /apps/admin (internal console)
            - approvals
            - task runs
            - memory explorer
            - drive sync
            - routing config
            - audit log
Core flows

Inbound task flow

WhatsApp webhook arrives.

API validates signature/token and stores message idempotently.

Queue job inbound.whatsapp.received.

Worker creates/continues task, loads conversation state + memory + relevant Drive chunks.

Lead orchestrator plans, delegates bounded sub-tasks, synthesizes response.

If action required and risky, create approval request.

If safe or approved, execute adapter and send outbound message.

Drive sync flow

Manual sync, scheduled sync, or changes-feed poll.

Fetch file metadata/revision, parse content, chunk, embed.

Upsert documents, document_chunks, memory_items, source_refs.

Mark stale chunks superseded on revision change.

Daily suggestion flow

Scheduler runs at user local time.

Gather open tasks, pending approvals, recent conversations, reminders, daily_context items, latest Drive docs.

Suggestion agent ranks useful nudges.

Send concise WhatsApp summary unless quiet hours block.

Approval flow

Orchestrator proposes action with risk level + reason.

Approval record created with token, TTL, scope, and action fingerprint.

User approves in WhatsApp or admin.

Action executor checks approval state and idempotency key before execution.

Outcome written to audit log and event log.

Data model
Core operational entities

users — profile, timezone, quiet hours, preferences

channels — whatsapp phone identifiers, provider config

conversations — logical chat threads

messages — inbound/outbound messages, provider ids, status

tasks — user requests and orchestration lifecycle

agent_runs — each lead/worker execution attempt

delegations — parent/child run relationships and contracts

approvals — approval requests, status, scope, expiry

action_runs — attempted adapter executions

reminders — scheduled nudges/actions

daily_context_items — notes, todos, routines, events, preferences

knowledge_sources — Google Drive connections and source folders

documents — synced Drive files + metadata

document_chunks — chunk text + embedding + metadata

event_log — immutable domain events

audit_log — operator/security-relevant actions

outbox_messages — durable outbound sends for retries

Key indexes

messages(provider_message_id) unique

tasks(status, created_at desc)

approvals(status, expires_at)

documents(source_id, external_file_id, revision_id) unique

document_chunks using hnsw (embedding vector_cosine_ops)

daily_context_items(user_id, effective_date)

outbox_messages(status, next_attempt_at)

Shared semantic memory schema

Use two memory layers:

A. Episodic / conversation memory

memory_items

id

user_id

kind (fact, preference, plan, summary, artifact_ref, constraint)

summary

body

salience

confidence

source_type (conversation, drive, system, action)

source_ref

embedding

last_seen_at

expires_at

B. Graph / relationship memory

memory_edges

from_memory_id

to_memory_id

relation (supports, contradicts, updates, belongs_to, requires)

weight

C. Retrieved knowledge

document_chunks

document_id

chunk_index

text

tokens

embedding

source_span

metadata_json

Retrieval strategy

Step 1: conversation + task filters

Step 2: vector search over memory_items and document_chunks

Step 3: recency + salience + source-quality rerank

Step 4: pack results into agent context with source refs

Step 5: write back compressed summary after run

API contracts
External/provider routes

GET /webhooks/whatsapp
Provider verification.

POST /webhooks/whatsapp
Receives inbound messages and delivery updates.
Auth: provider signature/token.
Idempotency: provider_message_id.

Internal app routes

POST /api/v1/tasks
Create a task from admin or system input.
Body: { userId, prompt, channel?: "whatsapp"|"admin", sourceMessageId? }

GET /api/v1/tasks/:taskId
Returns task state, current plan, child runs, outputs, approvals.

POST /api/v1/approvals/:approvalId/decision
Body: { decision: "approved"|"rejected", actorId, note? }

POST /api/v1/drive/sync
Body: { sourceId, full?: boolean }

GET /api/v1/memory/search?q=...&userId=...
Returns relevant memory items and document chunks.

POST /api/v1/suggestions/run
Body: { userId, reason?: "schedule"|"manual"|"new_context" }

GET /healthz
Liveness.
GET /readyz readiness for DB/Redis/provider connectivity.

Event model
whatsapp.message.received
whatsapp.message.delivery_updated
task.created
task.planned
task.delegated
agent.run.started
agent.run.completed
agent.run.failed
memory.upserted
drive.document.synced
approval.requested
approval.resolved
approval.expired
action.run.requested
action.run.executed
action.run.failed
suggestion.generated
suggestion.sent
outbox.message.queued
outbox.message.sent
outbox.message.failed
audit.recorded

Each event carries:

event_id

occurred_at

correlation_id

causation_id

tenant/user ids

actor

payload_json

The archive repeatedly stresses retries with idempotency, backpressure, observability, and failure drills; that is why every async event here is durable, correlation-id based, and safe to replay without duplicate side effects.

Repo tree
wa-orchestrator/
├─ README.md
├─ .env.example
├─ package.json
├─ pnpm-workspace.yaml
├─ turbo.json
├─ tsconfig.base.json
├─ docker-compose.yml
├─ .github/
│  └─ workflows/
│     └─ ci.yml
├─ apps/
│  ├─ api/
│  │  ├─ package.json
│  │  ├─ tsconfig.json
│  │  └─ src/
│  │     ├─ index.ts
│  │     ├─ app.ts
│  │     ├─ lib/
│  │     │  ├─ db.ts
│  │     │  ├─ redis.ts
│  │     │  ├─ env.ts
│  │     │  ├─ logger.ts
│  │     │  └─ metrics.ts
│  │     └─ modules/
│  │        ├─ webhooks/whatsapp.routes.ts
│  │        ├─ tasks/task.routes.ts
│  │        ├─ approvals/approval.routes.ts
│  │        ├─ drive/drive.routes.ts
│  │        ├─ memory/memory.routes.ts
│  │        ├─ health/health.routes.ts
│  │        ├─ orchestrator/
│  │        │  ├─ orchestrator.service.ts
│  │        │  ├─ agent-registry.ts
│  │        │  ├─ delegation.contract.ts
│  │        │  └─ stop-policy.ts
│  │        ├─ groq/
│  │        │  ├─ groq.client.ts
│  │        │  ├─ router.ts
│  │        │  └─ prompts/
│  │        │     ├─ lead.system.txt
│  │        │     ├─ research.worker.system.txt
│  │        │     ├─ reflection.system.txt
│  │        │     └─ suggestion.system.txt
│  │        ├─ memory/
│  │        │  ├─ memory.repository.ts
│  │        │  ├─ retrieval.service.ts
│  │        │  └─ embedding.service.ts
│  │        ├─ drive/
│  │        │  ├─ drive.service.ts
│  │        │  ├─ sync.service.ts
│  │        │  └─ parsers/
│  │        │     ├─ google-doc.parser.ts
│  │        │     ├─ pdf.parser.ts
│  │        │     └─ text.parser.ts
│  │        ├─ actions/
│  │        │  ├─ approval.service.ts
│  │        │  ├─ executor.service.ts
│  │        │  └─ adapters/
│  │        │     ├─ whatsapp-send.adapter.ts
│  │        │     ├─ webhook.adapter.ts
│  │        │     ├─ email.adapter.ts
│  │        │     └─ reminder.adapter.ts
│  │        └─ suggestions/daily-context.service.ts
│  ├─ worker/
│  │  ├─ package.json
│  │  ├─ tsconfig.json
│  │  └─ src/
│  │     ├─ index.ts
│  │     └─ jobs/
│  │        ├─ inbound.job.ts
│  │        ├─ orchestrate.job.ts
│  │        ├─ drive-sync.job.ts
│  │        ├─ suggestion.job.ts
│  │        └─ outbox.job.ts
│  └─ admin/
│     ├─ package.json
│     └─ app/
│        ├─ page.tsx
│        ├─ approvals/page.tsx
│        ├─ tasks/[id]/page.tsx
│        ├─ memory/page.tsx
│        └─ audit/page.tsx
├─ packages/
│  ├─ contracts/
│  │  └─ src/
│  │     ├─ api.ts
│  │     ├─ events.ts
│  │     └─ types.ts
│  ├─ config/
│  │  └─ src/env.ts
│  └─ observability/
│     └─ src/
│        ├─ logger.ts
│        └─ tracing.ts
├─ infra/
│  ├─ migrations/
│  │  └─ 001_init.sql
│  ├─ docker/
│  │  ├─ api.Dockerfile
│  │  ├─ worker.Dockerfile
│  │  └─ admin.Dockerfile
│  └─ gcp/
│     ├─ cloudrun-api.yaml
│     ├─ cloudrun-worker.yaml
│     └─ scheduler-daily.yaml
└─ tests/
   ├─ e2e/
   │  ├─ whatsapp-approval.spec.ts
   │  └─ daily-suggestion.spec.ts
   └─ evals/
      ├─ research-quality.spec.ts
      ├─ action-safety.spec.ts
      └─ routing-cost-latency.spec.ts
Starter code
apps/api/src/modules/webhooks/whatsapp.routes.ts
import { FastifyPluginAsync } from "fastify";
import crypto from "node:crypto";
import { inboxQueue } from "../../lib/redis";

type WhatsAppWebhook = {
  entry?: Array<{
    changes?: Array<{
      value?: {
        messages?: Array<{
          id: string;
          from: string;
          timestamp: string;
          text?: { body: string };
          type: string;
        }>;
        statuses?: Array<any>;
      };
    }>;
  }>;
};

function verifySignature(rawBody: string, signature: string | undefined, appSecret: string) {
  if (!signature) return false;
  const expected = "sha256=" + crypto.createHmac("sha256", appSecret).update(rawBody).digest("hex");
  return crypto.timingSafeEqual(Buffer.from(expected), Buffer.from(signature));
}

export const whatsappRoutes: FastifyPluginAsync = async (app) => {
  app.get("/webhooks/whatsapp", async (req, reply) => {
    const q = req.query as Record<string, string>;
    if (
      q["hub.mode"] === "subscribe" &&
      q["hub.verify_token"] === app.config.WHATSAPP_VERIFY_TOKEN
    ) {
      return reply.code(200).send(q["hub.challenge"]);
    }
    return reply.code(403).send({ error: "verification failed" });
  });

  app.post("/webhooks/whatsapp", async (req, reply) => {
    const rawBody = JSON.stringify(req.body ?? {});
    const signature = req.headers["x-hub-signature-256"] as string | undefined;

    if (!verifySignature(rawBody, signature, app.config.WHATSAPP_APP_SECRET)) {
      return reply.code(401).send({ error: "bad signature" });
    }

    const body = req.body as WhatsAppWebhook;

    for (const entry of body.entry ?? []) {
      for (const change of entry.changes ?? []) {
        for (const msg of change.value?.messages ?? []) {
          await inboxQueue.add(
            "inbound.whatsapp.received",
            {
              providerMessageId: msg.id,
              from: msg.from,
              body: msg.text?.body ?? "",
              receivedAt: new Date(Number(msg.timestamp) * 1000).toISOString(),
            },
            {
              // idempotency: duplicate provider webhooks collapse to one job
              jobId: `wa:${msg.id}`,
              removeOnComplete: 1000,
              removeOnFail: 5000,
            }
          );
        }
      }
    }

    return reply.code(200).send({ ok: true });
  });
};
apps/api/src/modules/groq/router.ts
export type TaskType =
  | "classify"
  | "plan"
  | "retrieve"
  | "research"
  | "synthesize"
  | "reflect"
  | "suggest";

export type RouteInput = {
  taskType: TaskType;
  expectedTokens: number;
  latencyBudgetMs: number;
  costTier: "low" | "medium" | "high";
  toolUse: boolean;
};

export type ModelRoute = {
  primary: string;
  fallback: string[];
  maxTokens: number;
};

export function selectGroqRoute(input: RouteInput): ModelRoute {
  const FAST = process.env.GROQ_MODEL_FAST!;
  const REASONING = process.env.GROQ_MODEL_REASONING!;
  const SYNTHESIS = process.env.GROQ_MODEL_SYNTHESIS!;

  if (input.taskType === "classify" || input.taskType === "suggest") {
    return { primary: FAST, fallback: [REASONING], maxTokens: 1200 };
  }

  if (input.taskType === "retrieve" && input.latencyBudgetMs < 2000) {
    return { primary: FAST, fallback: [REASONING], maxTokens: 2000 };
  }

  if (input.taskType === "synthesize" || input.expectedTokens > 12000) {
    return { primary: SYNTHESIS, fallback: [REASONING, FAST], maxTokens: 16000 };
  }

  return { primary: REASONING, fallback: [FAST], maxTokens: 8000 };
}
apps/worker/src/jobs/orchestrate.job.ts
import { db } from "../lib/db";
import { selectGroqRoute } from "../../api/src/modules/groq/router";
import { approvalService } from "../../api/src/modules/actions/approval.service";
import { memoryRepository } from "../../api/src/modules/memory/memory.repository";
import { actionExecutor } from "../../api/src/modules/actions/executor.service";

type Delegation = {
  role: "research" | "retrieval" | "reflection";
  objective: string;
  outputSchema: Record<string, string>;
  toolsAllowed: string[];
  maxToolCalls: number;
};

const MAX_SUBAGENTS = 4;
const MAX_ORCHESTRATION_LOOPS = 3;

export async function orchestrateTask(taskId: string) {
  const task = await db.tasks.findById(taskId);
  if (!task) throw new Error(`task not found: ${taskId}`);

  const memory = await memoryRepository.retrieveContext(task.userId, task.prompt, 20);

  // lead planner
  const route = selectGroqRoute({
    taskType: "plan",
    expectedTokens: 4000,
    latencyBudgetMs: 5000,
    costTier: "medium",
    toolUse: true,
  });

  const plan = await db.llm.planWithModel(route.primary, {
    taskPrompt: task.prompt,
    memory,
    constraints: {
      maxSubagents: MAX_SUBAGENTS,
      maxLoops: MAX_ORCHESTRATION_LOOPS,
      approvalRequiredFor: ["email.send", "webhook.post", "calendar.write"],
    },
  });

  await memoryRepository.savePlan(task.userId, task.id, plan.summary);

  const delegations: Delegation[] = plan.delegations.slice(0, MAX_SUBAGENTS);

  const childResults = [];
  for (const d of delegations) {
    const result = await db.llm.runWorker({
      role: d.role,
      objective: d.objective,
      outputSchema: d.outputSchema,
      toolsAllowed: d.toolsAllowed,
      maxToolCalls: d.maxToolCalls,
    });
    childResults.push(result);
  }

  const synthesisRoute = selectGroqRoute({
    taskType: "synthesize",
    expectedTokens: 10000,
    latencyBudgetMs: 10000,
    costTier: "high",
    toolUse: false,
  });

  const synthesis = await db.llm.summarizeWithModel(synthesisRoute.primary, {
    taskPrompt: task.prompt,
    childResults,
    memory,
  });

  await memoryRepository.upsertSummary(task.userId, task.id, synthesis.summary);

  if (synthesis.proposedAction) {
    if (approvalService.requiresApproval(synthesis.proposedAction.type)) {
      return approvalService.requestApproval({
        userId: task.userId,
        taskId: task.id,
        actionType: synthesis.proposedAction.type,
        payload: synthesis.proposedAction.payload,
        reason: synthesis.proposedAction.reason,
      });
    }

    return actionExecutor.execute({
      taskId: task.id,
      userId: task.userId,
      actionType: synthesis.proposedAction.type,
      payload: synthesis.proposedAction.payload,
      approvalId: null,
    });
  }

  return db.outbox.enqueueWhatsAppReply(task.userId, synthesis.replyText);
}
apps/api/src/modules/actions/approval.service.ts
import crypto from "node:crypto";
import { db } from "../../lib/db";

type RequestApprovalInput = {
  userId: string;
  taskId: string;
  actionType: string;
  payload: Record<string, unknown>;
  reason: string;
};

export const approvalService = {
  requiresApproval(actionType: string) {
    return [
      "email.send",
      "webhook.post",
      "calendar.write",
      "document.share",
      "contact.message",
    ].includes(actionType);
  },

  async requestApproval(input: RequestApprovalInput) {
    const token = crypto.randomUUID();
    const fingerprint = crypto
      .createHash("sha256")
      .update(JSON.stringify({ actionType: input.actionType, payload: input.payload }))
      .digest("hex");

    const approval = await db.approvals.insert({
      user_id: input.userId,
      task_id: input.taskId,
      token,
      status: "pending",
      action_type: input.actionType,
      payload_json: input.payload,
      reason: input.reason,
      action_fingerprint: fingerprint,
      expires_at: new Date(Date.now() + 15 * 60 * 1000),
    });

    await db.outbox.enqueueWhatsAppReply(
      input.userId,
      `Approval needed: ${input.actionType}\nReason: ${input.reason}\nReply: APPROVE ${approval.id} or REJECT ${approval.id}`
    );

    return approval;
  },

  async applyDecision(approvalId: string, decision: "approved" | "rejected", actorId: string) {
    return db.approvals.update(approvalId, {
      status: decision,
      decided_by: actorId,
      decided_at: new Date(),
    });
  },

  async assertApproved(approvalId: string | null) {
    if (!approvalId) return;
    const approval = await db.approvals.findById(approvalId);
    if (!approval || approval.status !== "approved") {
      throw new Error("approval missing or not approved");
    }
    if (new Date(approval.expires_at).getTime() < Date.now()) {
      throw new Error("approval expired");
    }
  },
};
infra/migrations/001_init.sql
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE users (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  whatsapp_phone TEXT UNIQUE NOT NULL,
  display_name TEXT,
  timezone TEXT NOT NULL DEFAULT 'UTC',
  quiet_hours JSONB NOT NULL DEFAULT '{}'::jsonb,
  preferences_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE conversations (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(id),
  channel TEXT NOT NULL DEFAULT 'whatsapp',
  external_thread_id TEXT,
  last_message_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE messages (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  conversation_id UUID NOT NULL REFERENCES conversations(id),
  direction TEXT NOT NULL CHECK (direction IN ('inbound', 'outbound')),
  provider_message_id TEXT UNIQUE,
  body TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'accepted',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE tasks (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(id),
  conversation_id UUID REFERENCES conversations(id),
  prompt TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'queued',
  plan_summary TEXT,
  output_text TEXT,
  correlation_id UUID NOT NULL DEFAULT gen_random_uuid(),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE approvals (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(id),
  task_id UUID NOT NULL REFERENCES tasks(id),
  token TEXT UNIQUE NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('pending','approved','rejected','expired')),
  action_type TEXT NOT NULL,
  payload_json JSONB NOT NULL,
  reason TEXT NOT NULL,
  action_fingerprint TEXT NOT NULL,
  expires_at TIMESTAMPTZ NOT NULL,
  decided_by UUID,
  decided_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE documents (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(id),
  source_type TEXT NOT NULL DEFAULT 'google_drive',
  external_file_id TEXT NOT NULL,
  revision_id TEXT NOT NULL,
  title TEXT NOT NULL,
  mime_type TEXT NOT NULL,
  source_url TEXT,
  metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (user_id, external_file_id, revision_id)
);

CREATE TABLE document_chunks (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
  chunk_index INT NOT NULL,
  text TEXT NOT NULL,
  tokens INT NOT NULL DEFAULT 0,
  metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  embedding VECTOR(1536),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (document_id, chunk_index)
);

CREATE INDEX document_chunks_embedding_idx
  ON document_chunks USING hnsw (embedding vector_cosine_ops);

CREATE TABLE memory_items (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES users(id),
  kind TEXT NOT NULL,
  summary TEXT NOT NULL,
  body TEXT,
  salience REAL NOT NULL DEFAULT 0.5,
  confidence REAL NOT NULL DEFAULT 0.5,
  source_type TEXT NOT NULL,
  source_ref TEXT,
  embedding VECTOR(1536),
  last_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX memory_items_embedding_idx
  ON memory_items USING hnsw (embedding vector_cosine_ops);
docker-compose.yml
version: "3.9"

services:
  postgres:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_USER: app
      POSTGRES_PASSWORD: app
      POSTGRES_DB: wa_orchestrator
    ports:
      - "5432:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"

  api:
    build:
      context: .
      dockerfile: infra/docker/api.Dockerfile
    env_file: .env
    depends_on:
      - postgres
      - redis
    ports:
      - "3000:3000"
    command: ["pnpm", "--filter", "api", "dev"]

  worker:
    build:
      context: .
      dockerfile: infra/docker/worker.Dockerfile
    env_file: .env
    depends_on:
      - postgres
      - redis
    command: ["pnpm", "--filter", "worker", "dev"]

volumes:
  pgdata:
.github/workflows/ci.yml
name: ci

on:
  push:
    branches: [main]
  pull_request:

jobs:
  test:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: pgvector/pgvector:pg16
        ports: ["5432:5432"]
        env:
          POSTGRES_USER: app
          POSTGRES_PASSWORD: app
          POSTGRES_DB: wa_orchestrator
        options: >-
          --health-cmd="pg_isready -U app -d wa_orchestrator"
          --health-interval=10s
          --health-timeout=5s
          --health-retries=5
      redis:
        image: redis:7-alpine
        ports: ["6379:6379"]

    env:
      DATABASE_URL: postgresql://app:app@localhost:5432/wa_orchestrator
      REDIS_URL: redis://localhost:6379
      GROQ_API_KEY: test
      GROQ_MODEL_FAST: fast-model
      GROQ_MODEL_REASONING: reasoning-model
      GROQ_MODEL_SYNTHESIS: synthesis-model

    steps:
      - uses: actions/checkout@v4
      - uses: pnpm/action-setup@v3
        with:
          version: 9
      - uses: actions/setup-node@v4
        with:
          node-version: 20
          cache: pnpm
      - run: pnpm install --frozen-lockfile
      - run: pnpm lint
      - run: pnpm typecheck
      - run: psql "$DATABASE_URL" -f infra/migrations/001_init.sql
      - run: pnpm test
      - run: docker build -f infra/docker/api.Dockerfile .
      - run: docker build -f infra/docker/worker.Dockerfile .
Environment contract
PORT=3000
DATABASE_URL=postgresql://app:app@localhost:5432/wa_orchestrator
REDIS_URL=redis://localhost:6379
GROQ_API_KEY=
GROQ_MODEL_FAST=
GROQ_MODEL_REASONING=
GROQ_MODEL_SYNTHESIS=
WHATSAPP_VERIFY_TOKEN=
WHATSAPP_APP_SECRET=
WHATSAPP_ACCESS_TOKEN=
WHATSAPP_PHONE_NUMBER_ID=
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
GOOGLE_REDIRECT_URI=
ENCRYPTION_KEY=
SENTRY_DSN=
OTEL_EXPORTER_OTLP_ENDPOINT=
Local run commands
pnpm install
cp .env.example .env
docker compose up -d postgres redis
psql "$DATABASE_URL" -f infra/migrations/001_init.sql
pnpm --filter api dev
pnpm --filter worker dev
pnpm test
Deployment plan

Staging

Cloud Run service: wa-orchestrator-api

Cloud Run service: wa-orchestrator-worker

Cloud SQL Postgres with pgvector

Memorystore Redis

Secret Manager for Groq, WhatsApp, Google OAuth secrets

Cloud Scheduler hits daily suggestion endpoint or enqueues job

Optional admin app on Cloud Run or Vercel

Production

Same topology, separate project/database/Redis

Blue/green or canary via Cloud Run revisions

Post-deploy smoke test:

/healthz

webhook verification

enqueue test task

approval create/resolve test

outbound dry run

Release path

PR runs CI

Merge to main builds containers

Apply DB migration

Deploy new revision to staging

Run smoke tests

Promote to prod with 10% traffic

Observe error rate/queue lag

Roll forward or instant rollback to previous revision

The package is explicit that deployment, QA, secrets handling, observability, and rollback are part of the shipped definition, not optional extras.

QA plan

Unit

Groq router policy

approval gating and expiry

stop conditions

prompt contract validation

daily suggestion ranking

Integration

WhatsApp webhook deduplication

Drive sync revision handling

pgvector retrieval + memory writeback

approval → action execution happy path

outbox retry behavior

E2E

Inbound WhatsApp research task

Risky action requests approval

User approves in WhatsApp

Action executes exactly once

Daily suggestion generated from fresh context

Eval harness

20 gold tasks across:

retrieval accuracy

synthesis quality

citation/source coverage

action safety

duplicate-action prevention

routing latency/cost

Save per-run:

chosen model alias

tokens

latency

tool calls

success/fail

approval outcome

Stop conditions for agent loops:

budget exhausted

enough evidence collected

duplicate findings

waiting on approval

provider degraded

The reference pack says serious builds need smoke tests, edge cases, auth/security validation, deployment verification, and failure awareness.

Failure modes and safe fallbacks

WhatsApp webhook duplication
Cause: provider retries.
Mitigation: unique provider_message_id, queue jobId, idempotent task creation.
Fallback: ack 200 once persisted, ignore duplicates.

Groq/provider outage
Cause: model/API unavailable.
Mitigation: router fallback models, timeout budget, circuit breaker, retry with jitter.
Fallback: send user “I’m delayed, still working” and queue retry.

Duplicate action execution
Cause: retries after approval or adapter timeout.
Mitigation: action_fingerprint, idempotency key, outbox pattern, action status transitions.
Fallback: refuse second execution if fingerprint already completed.

Stale Drive memory
Cause: document revision changed after indexing.
Mitigation: revision-aware upserts and stale-chunk invalidation.
Fallback: answer with freshness note and resync.

Queue backlog / retry storm
Cause: provider degradation or bad job.
Mitigation: per-queue concurrency, DLQ, retry caps, queue lag alerts.
Fallback: degrade to human-visible delays and disable proactive jobs.

Conflicting plans between workers
Cause: subagents diverge.
Mitigation: explicit delegation contracts, reflection pass, confidence scoring, contradiction edges in memory.
Fallback: lead agent asks user or marks uncertainty instead of acting.

Approval abuse / token leakage
Cause: leaked token or replay.
Mitigation: short TTL, scoped approvals, encrypted secret storage, replay-safe decision endpoint. The archive’s OAuth guidance specifically warns about token leakage, refresh-token misuse, rotation, and revocation concerns.

Observability blind spots
Cause: no correlation IDs.
Mitigation: every task/run/action/event carries correlation_id and causation_id.
Fallback: manual replay from event log.

The archive’s universal workflow explicitly calls for quotas, retries/timeouts, idempotency, observability, and failure drills, and warns about backpressure gaps and retry amplification during outages.

Operator notes

Track these dashboards from day one:

webhook ack latency

queue lag by job type

task completion rate

approval requested/approved/rejected

duplicate webhook drops

action execution success rate

retrieval latency + top-k hit rate

token usage and cost per task

Drive sync freshness

outbound send failures

First vertical slices

Slice 1
Webhook ingest, task creation, queue, simple lead agent, WhatsApp reply, health checks, staging deploy.

Slice 2
Drive sync, chunking, embeddings, retrieval, summary generation, write-back to Drive.

Slice 3
Approval service, action executor, audit log, idempotent outbox.

Slice 4
Daily-context suggestions, scheduler, quiet hours, eval harness, routing tuning.

Next Actions

 Stand up the monorepo and apply 001_init.sql

 Wire Meta WhatsApp Cloud API webhook + outbound send adapter

 Implement Drive OAuth + sync job + chunk/embed pipeline

 Add the lead orchestrator, 3 worker roles, and approval-gated executor

 Deploy staging on Cloud Run and run the smoke checklist

Assumptions

 Personal assistant first, team/multi-user later

 Meta WhatsApp Cloud API is the messaging provider

 Google Drive is the primary user-facing document system; app DB holds state and indexes

 Groq model names are provided via env aliases rather than hard-coded

 Cloud Run / Cloud SQL / Redis is acceptable for MVP hosting

Reference packs used:

looks like you timed out or ran out of context...you last sentence is incomplete and says "Reference packs used:" and nothing below. Also provide a zip of this build
Thought for 5m 1s

You’re right — that line got cut off.

The corrected ending is:

Reference packs used: System_Design_Master_Archive_2026, Systems_Architect_Final_Package_Merged, Claude Multi Agent Research Design.pdf, and System Design Guide(Original).pdf.

I also added a bootstrap script to the canvas named wa-orchestrator-bootstrap.sh. It generates the full repo scaffold and then creates wa-orchestrator.zip locally when you run it.

What’s in that scaffold:

monorepo structure

API, worker, and admin app skeletons

SQL migration for core tables

Groq routing starter

approvals/action-execution scaffolding

Google Drive sync placeholder

Docker + docker-compose

GitHub Actions CI

test skeletons

architecture/product docs

That matches the uploaded builder guidance to include actual deliverables such as repo structure, code scaffolds, SQL tables, API route stubs, Docker/deployment config, and QA/deploy coverage rather than stopping at architecture prose.

Run it like this after copying it out of the canvas:

chmod +x wa-orchestrator-bootstrap.sh
./wa-orchestrator-bootstrap.sh

That will create:

wa-orchestrator/

wa-orchestrator.zip

I can also turn that scaffold into a tighter production-ready v1 repo next, with real Postgres/Redis wiring and a working Meta WhatsApp outbound adapter.
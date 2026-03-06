# WhatsApp Multi-Agent Orchestrator Assistant

Standalone WhatsApp-based orchestrator assistant with:
- Meta WhatsApp Cloud API inbound/outbound
- Fastify API + BullMQ worker modular monolith
- Groq model routing + safe fallback heuristics
- Postgres + pgvector semantic memory
- Google Drive OAuth, sync, and document indexing scaffold
- approval-gated action execution
- durable outbox for WhatsApp delivery
- daily-context suggestions
- Docker, CI, and Cloud Run starter manifests

## Quick start
```bash
bash setup.sh
cd wa-orchestrator
cp .env.example .env
pnpm install
docker compose up -d postgres redis
psql "$DATABASE_URL" -f infra/migrations/001_init.sql
psql "$DATABASE_URL" -f infra/migrations/002_security_and_outbox.sql
pnpm dev
```

## Main flows
1. WhatsApp webhook receives inbound message and enqueues work.
2. Worker creates or resumes a task and runs the orchestrator.
3. Orchestrator retrieves memory, routes to Groq, and either replies, queues an approval, or executes a safe action.
4. All user-facing WhatsApp sends go through a durable outbox.
5. Drive OAuth stores encrypted tokens, sync jobs export Google Docs, chunk text, and index document content.

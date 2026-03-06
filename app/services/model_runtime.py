"""Single model router entrypoint for all runtime provider interactions."""

from __future__ import annotations

from app.models.base import GenerateRequest, TraceMetadata
from app.models.router import ModelRouter

router = ModelRouter()


def generate_response(
    *,
    messages: list[dict[str, object]],
    run_id: str,
    agent_id: str,
    task_type: str,
    budget_context: dict[str, object],
    tools: list[dict[str, object]] | None = None,
    response_schema: dict[str, object] | None = None,
    temperature: float = 0.2,
    max_tokens: int = 1024,
) -> dict[str, object]:
    """Router-only generation entrypoint with standardized trace metadata."""

    trace = TraceMetadata(
        run_id=run_id,
        agent_id=agent_id,
        task_type=task_type,
        budget_context=budget_context,
    )
    request = GenerateRequest(
        messages=messages,
        tools=tools,
        response_schema=response_schema,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return router.generate(request, trace)


def generate_embeddings(
    *,
    texts: list[str],
    run_id: str,
    agent_id: str,
    task_type: str,
    budget_context: dict[str, object],
) -> list[list[float]]:
    """Router-only embedding entrypoint with standardized trace metadata."""

    trace = TraceMetadata(
        run_id=run_id,
        agent_id=agent_id,
        task_type=task_type,
        budget_context=budget_context,
    )
    return router.embed(texts, trace)

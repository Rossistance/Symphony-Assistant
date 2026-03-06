"""Orchestration policy selection and runtime guardrails."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.config import OrchestratorConfig, orchestrator_config


class ExecutionMode(StrEnum):
    """Execution autonomy mode for a user task group."""

    DEFAULT = "default"
    SLOW = "slow"


@dataclass(frozen=True)
class OrchestrationPolicy:
    """Lead-agent policy knobs derived from the selected execution mode."""

    mode: ExecutionMode
    preserve_canonical_context: bool
    preserve_plan_artifact: bool
    delegate_bounded_support_only: bool
    ingest_condensed_outputs_only: bool
    verification_checkpoints: int
    high_impact_verification_depth: int


def _normalize(text: str) -> str:
    return " ".join(text.casefold().split())


def detect_execution_mode(
    user_message: str,
    *,
    config: OrchestratorConfig = orchestrator_config,
) -> ExecutionMode:
    """Selects slow mode when configured trigger phrases are present in the message."""

    normalized_message = _normalize(user_message)
    for phrase in config.slow_mode_trigger_phrases:
        if _normalize(phrase) in normalized_message:
            return ExecutionMode.SLOW
    return ExecutionMode.DEFAULT


def policy_for_mode(mode: ExecutionMode) -> OrchestrationPolicy:
    """Returns guardrail policy values for the execution mode."""

    if mode is ExecutionMode.SLOW:
        return OrchestrationPolicy(
            mode=mode,
            preserve_canonical_context=True,
            preserve_plan_artifact=True,
            delegate_bounded_support_only=True,
            ingest_condensed_outputs_only=True,
            verification_checkpoints=3,
            high_impact_verification_depth=2,
        )

    return OrchestrationPolicy(
        mode=mode,
        preserve_canonical_context=False,
        preserve_plan_artifact=False,
        delegate_bounded_support_only=False,
        ingest_condensed_outputs_only=False,
        verification_checkpoints=1,
        high_impact_verification_depth=1,
    )


def policy_for_message(
    user_message: str,
    *,
    config: OrchestratorConfig = orchestrator_config,
) -> OrchestrationPolicy:
    """Convenience helper to derive a policy from a user message."""

    return policy_for_mode(detect_execution_mode(user_message, config=config))

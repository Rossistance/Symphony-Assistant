"""Runtime controls for parent/child agent execution."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from typing import Any

from app.services.orchestration_policy import ExecutionMode, policy_for_mode


class AgentRuntimeError(RuntimeError):
    """Base runtime error for agent orchestration failures."""


class AgentNotFoundError(AgentRuntimeError):
    """Raised when an unknown agent id is used."""


class AgentLimitExceededError(AgentRuntimeError):
    """Raised when runtime quota or timeout limits are exceeded."""


class ChildSynthesisRequiredError(AgentRuntimeError):
    """Raised when unsupervised worker output is requested."""


@dataclass(frozen=True)
class RuntimeEvent:
    """Lifecycle event emitted by the runtime."""

    event_type: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class AgentContext:
    """Execution context attached to a running agent."""

    agent_id: str
    parent_agent_id: str | None
    role: str
    objective: str
    constraints: list[str]
    output_contract: str
    correlation_id: str
    safety_scope: dict[str, Any]
    max_tool_calls: int
    max_runtime_sec: int
    started_at: float
    execution_mode: ExecutionMode = ExecutionMode.DEFAULT


@dataclass
class AgentState:
    """Mutable runtime state for an agent."""

    context: AgentContext
    tool_calls_used: int = 0
    completed: bool = False
    high_impact_verification_checks: int = 0


@dataclass
class ChildOutputRecord:
    """Tracks child output lifecycle and parent synthesis."""

    child_agent_id: str
    parent_agent_id: str
    raw_output: str | None = None
    synthesized_output: str | None = None


@dataclass
class AgentRuntime:
    """In-memory runtime with parent/child spawning and guardrails."""

    clock: Any = time.monotonic

    def __post_init__(self) -> None:
        self.events: list[RuntimeEvent] = []
        self._agents: dict[str, AgentState] = {}
        self._child_outputs: dict[str, ChildOutputRecord] = {}

    def register_lead_agent(
        self,
        *,
        agent_id: str,
        role: str,
        objective: str,
        constraints: list[str],
        output_contract: str,
        correlation_id: str,
        safety_scope: dict[str, Any],
        max_tool_calls: int,
        max_runtime_sec: int,
        execution_mode: ExecutionMode = ExecutionMode.DEFAULT,
    ) -> str:
        """Registers a lead/root agent that can spawn child workers."""

        context = AgentContext(
            agent_id=agent_id,
            parent_agent_id=None,
            role=role,
            objective=objective,
            constraints=list(constraints),
            output_contract=output_contract,
            correlation_id=correlation_id,
            safety_scope=dict(safety_scope),
            max_tool_calls=max_tool_calls,
            max_runtime_sec=max_runtime_sec,
            started_at=self.clock(),
            execution_mode=execution_mode,
        )
        self._agents[agent_id] = AgentState(context=context)
        return agent_id

    def spawn_agent(
        self,
        *,
        parent_agent_id: str,
        role: str,
        objective: str,
        constraints: list[str],
        max_tool_calls: int,
        max_runtime_sec: int,
    ) -> str:
        """Spawn a child worker inheriting parent safety and correlation scopes."""

        parent = self._get_agent(parent_agent_id)
        self._assert_within_limits(parent)
        self._validate_contract(objective, constraints)

        child_agent_id = f"agent-{uuid.uuid4()}"
        output_contract = (
            "Return only structured findings that satisfy the objective, obey all "
            "constraints, and are ready for parent synthesis."
        )
        context = AgentContext(
            agent_id=child_agent_id,
            parent_agent_id=parent_agent_id,
            role=role,
            objective=objective,
            constraints=list(constraints),
            output_contract=output_contract,
            correlation_id=parent.context.correlation_id,
            safety_scope=dict(parent.context.safety_scope),
            max_tool_calls=max_tool_calls,
            max_runtime_sec=max_runtime_sec,
            started_at=self.clock(),
            execution_mode=parent.context.execution_mode,
        )
        self._agents[child_agent_id] = AgentState(context=context)
        self._child_outputs[child_agent_id] = ChildOutputRecord(
            child_agent_id=child_agent_id,
            parent_agent_id=parent_agent_id,
        )
        self._emit(
            "agent.spawned",
            {
                "parent_agent_id": parent_agent_id,
                "child_agent_id": child_agent_id,
                "role": role,
                "objective": objective,
                "constraints": list(constraints),
                "max_tool_calls": max_tool_calls,
                "max_runtime_sec": max_runtime_sec,
                "correlation_id": parent.context.correlation_id,
                "safety_scope": dict(parent.context.safety_scope),
                "output_contract": output_contract,
            },
        )
        return child_agent_id

    def record_tool_call(self, *, agent_id: str) -> None:
        """Consumes a single tool-call quota after validating runtime limits."""

        state = self._get_agent(agent_id)
        self._assert_within_limits(state)
        if state.tool_calls_used >= state.context.max_tool_calls:
            raise AgentLimitExceededError(f"agent '{agent_id}' exceeded maxToolCalls")
        state.tool_calls_used += 1

    def submit_child_output(self, *, child_agent_id: str, output: str) -> None:
        """Stores raw child output for explicit parent synthesis."""

        if child_agent_id not in self._child_outputs:
            raise AgentNotFoundError(f"child agent '{child_agent_id}' not found")
        self._child_outputs[child_agent_id].raw_output = output

    def synthesize_child_output(self, *, parent_agent_id: str, child_agent_id: str, synthesized_output: str) -> None:
        """Allows only parent/lead agents to approve synthesized child output."""

        if child_agent_id not in self._child_outputs:
            raise AgentNotFoundError(f"child agent '{child_agent_id}' not found")

        record = self._child_outputs[child_agent_id]
        if record.parent_agent_id != parent_agent_id:
            raise ChildSynthesisRequiredError("only the parent/lead agent may synthesize child output")
        if not record.raw_output:
            raise ChildSynthesisRequiredError("cannot synthesize before child output is submitted")

        record.synthesized_output = synthesized_output

    def record_high_impact_checkpoint(self, *, agent_id: str) -> None:
        """Records a verification checkpoint before a high-impact action."""

        state = self._get_agent(agent_id)
        self._assert_within_limits(state)
        state.high_impact_verification_checks += 1

    def can_execute_high_impact_action(self, *, agent_id: str) -> bool:
        """Returns whether high-impact actions satisfy current mode verification depth."""

        state = self._get_agent(agent_id)
        required = policy_for_mode(state.context.execution_mode).high_impact_verification_depth
        return state.high_impact_verification_checks >= required

    def get_child_result(self, *, child_agent_id: str) -> str:
        """Returns only parent-synthesized child output."""

        if child_agent_id not in self._child_outputs:
            raise AgentNotFoundError(f"child agent '{child_agent_id}' not found")

        record = self._child_outputs[child_agent_id]
        if not record.synthesized_output:
            raise ChildSynthesisRequiredError(
                "raw worker output is blocked until parent/lead synthesis is provided"
            )
        return record.synthesized_output

    def _get_agent(self, agent_id: str) -> AgentState:
        state = self._agents.get(agent_id)
        if state is None:
            raise AgentNotFoundError(f"agent '{agent_id}' not found")
        return state

    def _assert_within_limits(self, state: AgentState) -> None:
        elapsed = self.clock() - state.context.started_at
        if elapsed > state.context.max_runtime_sec:
            raise AgentLimitExceededError(f"agent '{state.context.agent_id}' exceeded maxRuntimeSec")

    def _validate_contract(self, objective: str, constraints: list[str]) -> None:
        if not objective.strip():
            raise AgentRuntimeError("objective is required for spawned agents")
        if not constraints:
            raise AgentRuntimeError("at least one constraint is required for strict child contracts")

    def _emit(self, event_type: str, payload: dict[str, Any]) -> None:
        self.events.append(RuntimeEvent(event_type=event_type, payload=payload))

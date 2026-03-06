"""Runtime controls for parent/child agent execution."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from app.services.orchestration_policy import ExecutionMode as OrchestrationExecutionMode, policy_for_mode


class AgentRuntimeError(RuntimeError):
    """Base runtime error for agent orchestration failures."""


class AgentNotFoundError(AgentRuntimeError):
    """Raised when an unknown agent id is used."""


class AgentLimitExceededError(AgentRuntimeError):
    """Raised when runtime quota or timeout limits are exceeded."""


class ChildSynthesisRequiredError(AgentRuntimeError):
    """Raised when unsupervised worker output is requested."""


class DelegationMode(str, Enum):
    """Execution mode selected by task-analysis policy."""

    SEQUENTIAL = "SEQUENTIAL"
    PARALLEL = "PARALLEL"
    HYBRID = "HYBRID"


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
    execution_mode: OrchestrationExecutionMode = OrchestrationExecutionMode.DEFAULT


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


@dataclass(frozen=True)
class TaskAnalysisInputs:
    """Inputs used to choose execution mode before delegation."""

    decomposition_count_estimate: int
    dependency_graph_density: float
    urgency: str
    token_budget: int
    tool_latency_profile: str


@dataclass(frozen=True)
class ExecutionModeDecision:
    """Persisted execution mode decision with machine-readable reason."""

    mode: DelegationMode
    reason_code: str
    reason: str
    task_analysis: TaskAnalysisInputs


@dataclass(frozen=True)
class DelegationTask:
    """Delegation unit used by orchestration mode scheduler."""

    task_id: str
    role: str
    objective: str
    constraints: list[str]
    max_tool_calls: int
    max_runtime_sec: int
    dependencies: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class DelegationExecutionPlan:
    """Mode-aware delegation schedule represented as staged fan-out batches."""

    mode: DelegationMode
    stages: list[list[str]]


# Backward-compatible alias for delegation mode enums used by tests/integrations.
ExecutionMode = DelegationMode

@dataclass
class AgentRuntime:
    """In-memory runtime with parent/child spawning and guardrails."""

    clock: Any = time.monotonic

    def __post_init__(self) -> None:
        self.events: list[RuntimeEvent] = []
        self._agents: dict[str, AgentState] = {}
        self._child_outputs: dict[str, ChildOutputRecord] = {}
        self._execution_decisions: dict[str, ExecutionModeDecision] = {}

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
        execution_mode: OrchestrationExecutionMode = OrchestrationExecutionMode.DEFAULT,
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

    def compute_task_analysis_inputs(
        self,
        *,
        decomposition_count_estimate: int,
        dependency_graph_density: float,
        urgency: str,
        token_budget: int,
        tool_latency_profile: str,
    ) -> TaskAnalysisInputs:
        """Builds normalized task-analysis inputs used before delegation."""

        normalized_urgency = urgency.strip().lower() or "normal"
        return TaskAnalysisInputs(
            decomposition_count_estimate=max(1, decomposition_count_estimate),
            dependency_graph_density=min(max(dependency_graph_density, 0.0), 1.0),
            urgency=normalized_urgency,
            token_budget=max(1, token_budget),
            tool_latency_profile=tool_latency_profile.strip().lower() or "mixed",
        )

    def select_execution_mode(self, *, parent_agent_id: str, task_analysis: TaskAnalysisInputs) -> ExecutionModeDecision:
        """Decides SEQUENTIAL/PARALLEL/HYBRID and persists reason for orchestration."""

        density = task_analysis.dependency_graph_density
        high_latency = task_analysis.tool_latency_profile in {"high", "network", "io-bound"}
        high_urgency = task_analysis.urgency in {"urgent", "high", "critical"}
        many_subtasks = task_analysis.decomposition_count_estimate >= 4

        if density >= 0.65:
            mode = DelegationMode.SEQUENTIAL
            reason_code = "dependent_steps"
            reason = "dependency graph is dense, so staged dependency pipeline is safer"
        elif density <= 0.3 and many_subtasks and (high_latency or high_urgency):
            mode = DelegationMode.PARALLEL
            reason_code = "independent_high_latency_work"
            reason = "branches are independent and latency/urgency favors fan-out"
        else:
            mode = DelegationMode.HYBRID
            reason_code = "mixed_dependencies"
            reason = "independent exploration with dependent synthesis is optimal"

        decision = ExecutionModeDecision(
            mode=mode,
            reason_code=reason_code,
            reason=reason,
            task_analysis=task_analysis,
        )
        self._execution_decisions[parent_agent_id] = decision
        self._emit(
            "execution.mode.selected",
            {
                "parent_agent_id": parent_agent_id,
                "mode": mode.value,
                "reason_code": reason_code,
                "reason": reason,
                "task_analysis": {
                    "decomposition_count_estimate": task_analysis.decomposition_count_estimate,
                    "dependency_graph_density": task_analysis.dependency_graph_density,
                    "urgency": task_analysis.urgency,
                    "token_budget": task_analysis.token_budget,
                    "tool_latency_profile": task_analysis.tool_latency_profile,
                },
            },
        )
        return decision

    def orchestrate_delegation(
        self,
        *,
        parent_agent_id: str,
        tasks: list[DelegationTask],
        task_analysis: TaskAnalysisInputs,
    ) -> DelegationExecutionPlan:
        """Spawns children according to selected mode (fan-out, pipeline, or hybrid)."""

        if not tasks:
            return DelegationExecutionPlan(mode=DelegationMode.SEQUENTIAL, stages=[])

        decision = self.select_execution_mode(parent_agent_id=parent_agent_id, task_analysis=task_analysis)
        if decision.mode is DelegationMode.PARALLEL:
            return self._execute_parallel(parent_agent_id=parent_agent_id, tasks=tasks)
        if decision.mode is DelegationMode.SEQUENTIAL:
            return self._execute_sequential(parent_agent_id=parent_agent_id, tasks=tasks)
        return self._execute_hybrid(parent_agent_id=parent_agent_id, tasks=tasks)

    def _execute_parallel(self, *, parent_agent_id: str, tasks: list[DelegationTask]) -> DelegationExecutionPlan:
        stage: list[str] = []
        for task in tasks:
            child_id = self.spawn_agent(
                parent_agent_id=parent_agent_id,
                role=task.role,
                objective=task.objective,
                constraints=task.constraints,
                max_tool_calls=task.max_tool_calls,
                max_runtime_sec=task.max_runtime_sec,
            )
            stage.append(child_id)
        return DelegationExecutionPlan(mode=DelegationMode.PARALLEL, stages=[stage])

    def _execute_sequential(self, *, parent_agent_id: str, tasks: list[DelegationTask]) -> DelegationExecutionPlan:
        ordered = self._topological_order(tasks)
        stages: list[list[str]] = []
        for task in ordered:
            child_id = self.spawn_agent(
                parent_agent_id=parent_agent_id,
                role=task.role,
                objective=task.objective,
                constraints=task.constraints,
                max_tool_calls=task.max_tool_calls,
                max_runtime_sec=task.max_runtime_sec,
            )
            stages.append([child_id])
        return DelegationExecutionPlan(mode=DelegationMode.SEQUENTIAL, stages=stages)

    def _execute_hybrid(self, *, parent_agent_id: str, tasks: list[DelegationTask]) -> DelegationExecutionPlan:
        by_id = {task.task_id: task for task in tasks}
        root_tasks = [task for task in tasks if not task.dependencies]
        if not root_tasks:
            return self._execute_sequential(parent_agent_id=parent_agent_id, tasks=tasks)

        first_stage: list[str] = []
        spawned = set()
        for task in root_tasks:
            child_id = self.spawn_agent(
                parent_agent_id=parent_agent_id,
                role=task.role,
                objective=task.objective,
                constraints=task.constraints,
                max_tool_calls=task.max_tool_calls,
                max_runtime_sec=task.max_runtime_sec,
            )
            first_stage.append(child_id)
            spawned.add(task.task_id)

        remaining = [by_id[task_id] for task_id in by_id if task_id not in spawned]
        sequential_tail = self._execute_sequential(parent_agent_id=parent_agent_id, tasks=remaining)
        return DelegationExecutionPlan(mode=DelegationMode.HYBRID, stages=[first_stage, *sequential_tail.stages])

    def _topological_order(self, tasks: list[DelegationTask]) -> list[DelegationTask]:
        by_id = {task.task_id: task for task in tasks}
        unresolved = set(by_id.keys())
        resolved: set[str] = set()
        order: list[DelegationTask] = []

        while unresolved:
            progressed = False
            for task_id in list(unresolved):
                task = by_id[task_id]
                deps = [dep for dep in task.dependencies if dep in by_id]
                if all(dep in resolved for dep in deps):
                    order.append(task)
                    resolved.add(task_id)
                    unresolved.remove(task_id)
                    progressed = True
            if not progressed:
                cyclic = sorted(unresolved)
                raise AgentRuntimeError(f"cyclic dependency detected across tasks: {', '.join(cyclic)}")
        return order

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

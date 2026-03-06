from __future__ import annotations

import unittest

from app.services.agent_runtime import (
    AgentRuntimeError,
    AgentLimitExceededError,
    AgentRuntime,
    ChildSynthesisRequiredError,
    DelegationTask,
    ExecutionMode,
)
from app.services.orchestration_policy import ExecutionMode


class FakeClock:
    def __init__(self, initial: float = 0.0):
        self.value = initial

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class AgentRuntimeTests(unittest.TestCase):
    def test_spawn_inherits_scope_and_records_lifecycle_event(self):
        clock = FakeClock()
        runtime = AgentRuntime(clock=clock)
        runtime.register_lead_agent(
            agent_id="agent-lead",
            role="lead",
            objective="Coordinate work",
            constraints=["never bypass policy"],
            output_contract="final answer",
            correlation_id="corr-123",
            safety_scope={"tenant_id": "acme", "classification": "internal"},
            max_tool_calls=5,
            max_runtime_sec=30,
        )

        child_id = runtime.spawn_agent(
            parent_agent_id="agent-lead",
            role="researcher",
            objective="Collect facts",
            constraints=["cite sources"],
            max_tool_calls=2,
            max_runtime_sec=10,
        )

        self.assertTrue(child_id.startswith("agent-"))
        self.assertEqual(runtime.events[-1].event_type, "agent.spawned")
        payload = runtime.events[-1].payload
        self.assertEqual(payload["parent_agent_id"], "agent-lead")
        self.assertEqual(payload["correlation_id"], "corr-123")
        self.assertEqual(payload["safety_scope"]["tenant_id"], "acme")
        self.assertIn("output_contract", payload)

    def test_runtime_enforces_tool_quota(self):
        runtime = AgentRuntime(clock=FakeClock())
        runtime.register_lead_agent(
            agent_id="lead",
            role="lead",
            objective="Coordinate",
            constraints=["safe"],
            output_contract="summary",
            correlation_id="corr",
            safety_scope={"tenant_id": "t1"},
            max_tool_calls=3,
            max_runtime_sec=60,
        )
        child_id = runtime.spawn_agent(
            parent_agent_id="lead",
            role="worker",
            objective="Analyze",
            constraints=["no pii"],
            max_tool_calls=1,
            max_runtime_sec=20,
        )

        runtime.record_tool_call(agent_id=child_id)
        with self.assertRaises(AgentLimitExceededError):
            runtime.record_tool_call(agent_id=child_id)

    def test_runtime_enforces_timeout(self):
        clock = FakeClock()
        runtime = AgentRuntime(clock=clock)
        runtime.register_lead_agent(
            agent_id="lead",
            role="lead",
            objective="Coordinate",
            constraints=["safe"],
            output_contract="summary",
            correlation_id="corr",
            safety_scope={"tenant_id": "t1"},
            max_tool_calls=3,
            max_runtime_sec=60,
        )
        child_id = runtime.spawn_agent(
            parent_agent_id="lead",
            role="worker",
            objective="Analyze",
            constraints=["no pii"],
            max_tool_calls=3,
            max_runtime_sec=5,
        )

        clock.advance(6)
        with self.assertRaises(AgentLimitExceededError):
            runtime.record_tool_call(agent_id=child_id)

    def test_requires_parent_synthesis_before_child_result(self):
        runtime = AgentRuntime(clock=FakeClock())
        runtime.register_lead_agent(
            agent_id="lead",
            role="lead",
            objective="Coordinate",
            constraints=["safe"],
            output_contract="summary",
            correlation_id="corr",
            safety_scope={"tenant_id": "t1"},
            max_tool_calls=3,
            max_runtime_sec=60,
        )
        child_id = runtime.spawn_agent(
            parent_agent_id="lead",
            role="worker",
            objective="Analyze",
            constraints=["no pii"],
            max_tool_calls=2,
            max_runtime_sec=20,
        )
        runtime.submit_child_output(child_agent_id=child_id, output="raw notes")

        with self.assertRaises(ChildSynthesisRequiredError):
            runtime.get_child_result(child_agent_id=child_id)

        runtime.synthesize_child_output(
            parent_agent_id="lead",
            child_agent_id=child_id,
            synthesized_output="parent-approved synthesis",
        )
        self.assertEqual(runtime.get_child_result(child_agent_id=child_id), "parent-approved synthesis")

    def test_select_execution_mode_persists_machine_readable_reason_and_emits_event(self):
        runtime = AgentRuntime(clock=FakeClock())
        runtime.register_lead_agent(
            agent_id="lead",
            role="lead",
            objective="Coordinate",
            constraints=["safe"],
            output_contract="summary",
            correlation_id="corr",
            safety_scope={"tenant_id": "t1"},
            max_tool_calls=10,
            max_runtime_sec=60,
        )
        inputs = runtime.compute_task_analysis_inputs(
            decomposition_count_estimate=6,
            dependency_graph_density=0.15,
            urgency="critical",
            token_budget=8000,
            tool_latency_profile="high",
        )

        decision = runtime.select_execution_mode(parent_agent_id="lead", task_analysis=inputs)

        self.assertEqual(decision.mode, ExecutionMode.PARALLEL)
        self.assertEqual(decision.reason_code, "independent_high_latency_work")
        self.assertEqual(runtime.events[-1].event_type, "execution.mode.selected")
        self.assertEqual(runtime.events[-1].payload["mode"], "PARALLEL")

    def test_orchestrate_delegation_parallel_uses_fan_out_stage(self):
        runtime = AgentRuntime(clock=FakeClock())
        runtime.register_lead_agent(
            agent_id="lead",
            role="lead",
            objective="Coordinate",
            constraints=["safe"],
            output_contract="summary",
            correlation_id="corr",
            safety_scope={"tenant_id": "t1"},
            max_tool_calls=10,
            max_runtime_sec=60,
        )
        tasks = [
            DelegationTask(
                task_id="a",
                role="researcher",
                objective="Branch A",
                constraints=["cite"],
                max_tool_calls=1,
                max_runtime_sec=10,
            ),
            DelegationTask(
                task_id="b",
                role="researcher",
                objective="Branch B",
                constraints=["cite"],
                max_tool_calls=1,
                max_runtime_sec=10,
            ),
        ]
        inputs = runtime.compute_task_analysis_inputs(
            decomposition_count_estimate=5,
            dependency_graph_density=0.1,
            urgency="urgent",
            token_budget=4000,
            tool_latency_profile="network",
        )

        plan = runtime.orchestrate_delegation(parent_agent_id="lead", tasks=tasks, task_analysis=inputs)

        self.assertEqual(plan.mode, ExecutionMode.PARALLEL)
        self.assertEqual(len(plan.stages), 1)
        self.assertEqual(len(plan.stages[0]), 2)

    def test_orchestrate_delegation_sequential_uses_dependency_pipeline(self):
        runtime = AgentRuntime(clock=FakeClock())
        runtime.register_lead_agent(
            agent_id="lead",
            role="lead",
            objective="Coordinate",
            constraints=["safe"],
            output_contract="summary",
            correlation_id="corr",
            safety_scope={"tenant_id": "t1"},
            max_tool_calls=10,
            max_runtime_sec=60,
        )
        tasks = [
            DelegationTask(
                task_id="prepare",
                role="planner",
                objective="Prepare",
                constraints=["safe"],
                max_tool_calls=1,
                max_runtime_sec=10,
            ),
            DelegationTask(
                task_id="synthesize",
                role="synthesizer",
                objective="Synthesize",
                constraints=["safe"],
                max_tool_calls=1,
                max_runtime_sec=10,
                dependencies=["prepare"],
            ),
        ]
        inputs = runtime.compute_task_analysis_inputs(
            decomposition_count_estimate=2,
            dependency_graph_density=0.9,
            urgency="normal",
            token_budget=2000,
            tool_latency_profile="mixed",
        )

        plan = runtime.orchestrate_delegation(parent_agent_id="lead", tasks=tasks, task_analysis=inputs)

        self.assertEqual(plan.mode, ExecutionMode.SEQUENTIAL)
        self.assertEqual(len(plan.stages), 2)
        self.assertEqual(len(plan.stages[0]), 1)
        self.assertEqual(len(plan.stages[1]), 1)

    def test_orchestrate_delegation_hybrid_fans_out_then_pipelines(self):
        runtime = AgentRuntime(clock=FakeClock())
        runtime.register_lead_agent(
            agent_id="lead",
            role="lead",
            objective="Coordinate",
            constraints=["safe"],
            output_contract="summary",
            correlation_id="corr",
            safety_scope={"tenant_id": "t1"},
            max_tool_calls=10,
            max_runtime_sec=60,
        )
        tasks = [
            DelegationTask(
                task_id="r1",
                role="researcher",
                objective="Research 1",
                constraints=["safe"],
                max_tool_calls=1,
                max_runtime_sec=10,
            ),
            DelegationTask(
                task_id="r2",
                role="researcher",
                objective="Research 2",
                constraints=["safe"],
                max_tool_calls=1,
                max_runtime_sec=10,
            ),
            DelegationTask(
                task_id="syn",
                role="synthesizer",
                objective="Synthesize",
                constraints=["safe"],
                max_tool_calls=1,
                max_runtime_sec=10,
                dependencies=["r1", "r2"],
            ),
        ]
        inputs = runtime.compute_task_analysis_inputs(
            decomposition_count_estimate=3,
            dependency_graph_density=0.5,
            urgency="normal",
            token_budget=2000,
            tool_latency_profile="mixed",
        )

        plan = runtime.orchestrate_delegation(parent_agent_id="lead", tasks=tasks, task_analysis=inputs)

        self.assertEqual(plan.mode, ExecutionMode.HYBRID)
        self.assertEqual(len(plan.stages), 2)
        self.assertEqual(len(plan.stages[0]), 2)
        self.assertEqual(len(plan.stages[1]), 1)

    def test_orchestrate_delegation_sequential_rejects_cycles(self):
        runtime = AgentRuntime(clock=FakeClock())
        runtime.register_lead_agent(
            agent_id="lead",
            role="lead",
            objective="Coordinate",
            constraints=["safe"],
            output_contract="summary",
            correlation_id="corr",
            safety_scope={"tenant_id": "t1"},
            max_tool_calls=10,
            max_runtime_sec=60,
        )
        tasks = [
            DelegationTask(
                task_id="a",
                role="worker",
                objective="A",
                constraints=["safe"],
                max_tool_calls=1,
                max_runtime_sec=10,
                dependencies=["b"],
            ),
            DelegationTask(
                task_id="b",
                role="worker",
                objective="B",
                constraints=["safe"],
                max_tool_calls=1,
                max_runtime_sec=10,
                dependencies=["a"],
            ),
        ]
        inputs = runtime.compute_task_analysis_inputs(
            decomposition_count_estimate=2,
            dependency_graph_density=0.9,
            urgency="normal",
            token_budget=1000,
            tool_latency_profile="low",
        )

        with self.assertRaises(AgentRuntimeError):
            runtime.orchestrate_delegation(parent_agent_id="lead", tasks=tasks, task_analysis=inputs)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import os
import unittest

from app.config import OrchestratorConfig
from app.services.orchestration_policy import ExecutionMode, detect_execution_mode, policy_for_message


class OrchestrationPolicyTests(unittest.TestCase):
    def setUp(self):
        self.original_env = os.environ.copy()

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self.original_env)

    def test_detects_default_triggers_with_both_apostrophe_variants(self):
        cfg = OrchestratorConfig()

        self.assertEqual(
            detect_execution_mode("I think this is a hard problem, please proceed", config=cfg),
            ExecutionMode.SLOW,
        )
        self.assertEqual(
            detect_execution_mode("Let’s take it slow and verify each step.", config=cfg),
            ExecutionMode.SLOW,
        )
        self.assertEqual(
            detect_execution_mode("Let's take it slow and verify each step.", config=cfg),
            ExecutionMode.SLOW,
        )

    def test_trigger_phrases_are_configurable(self):
        os.environ["SLOW_MODE_TRIGGER_PHRASES"] = "deep review mode|careful path"
        cfg = OrchestratorConfig()

        self.assertEqual(detect_execution_mode("please do deep review mode", config=cfg), ExecutionMode.SLOW)
        self.assertEqual(detect_execution_mode("normal fast task", config=cfg), ExecutionMode.DEFAULT)

    def test_slow_mode_policy_uses_more_verification_depth(self):
        policy = policy_for_message("this is a hard problem")

        self.assertEqual(policy.mode, ExecutionMode.SLOW)
        self.assertTrue(policy.preserve_canonical_context)
        self.assertTrue(policy.preserve_plan_artifact)
        self.assertTrue(policy.delegate_bounded_support_only)
        self.assertTrue(policy.ingest_condensed_outputs_only)
        self.assertGreater(policy.high_impact_verification_depth, 1)


if __name__ == "__main__":
    unittest.main()

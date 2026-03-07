import unittest

from app.messaging.agent_bus import AgentMessageBroker


class AgentMessageBrokerTests(unittest.TestCase):
    def test_send_emits_sent_event_with_metadata(self):
        broker = AgentMessageBroker()

        message = broker.send_agent_message(
            from_agent="planner",
            to_agent="worker",
            topic="task.created",
            payload_ref="artifact://task/123",
            correlation_id="corr-1",
            message_id="msg-1",
        )

        self.assertEqual(message.payload_ref, "artifact://task/123")
        self.assertEqual(message.correlation_id, "corr-1")
        self.assertEqual(len(broker.events), 1)
        event = broker.events[0]
        self.assertEqual(event.event_type, "agent.message.sent")
        self.assertEqual(event.payload["from_agent"], "planner")
        self.assertEqual(event.payload["to_agent"], "worker")
        self.assertEqual(event.payload["topic"], "task.created")

    def test_subscribe_redelivers_until_acknowledged(self):
        broker = AgentMessageBroker()
        broker.send_agent_message("planner", "worker", "task", "artifact://task/1", message_id="msg-1")

        first_delivery = broker.subscribe_agent_messages("worker")
        second_delivery = broker.subscribe_agent_messages("worker")

        self.assertEqual(first_delivery[0].message_id, "msg-1")
        self.assertEqual(first_delivery[0].delivery_attempts, 1)
        self.assertEqual(second_delivery[0].message_id, "msg-1")
        self.assertEqual(second_delivery[0].delivery_attempts, 2)

        broker.acknowledge_message("worker", "msg-1")
        third_delivery = broker.subscribe_agent_messages("worker")
        self.assertEqual(third_delivery, [])

    def test_same_message_id_processed_once_only(self):
        broker = AgentMessageBroker()
        broker.send_agent_message("planner", "worker", "task", "artifact://task/1", message_id="stable-1")
        broker.send_agent_message("planner", "worker", "task", "artifact://task/2", message_id="stable-1")

        handled_ids = []

        def _handler(message):
            handled_ids.append(message.message_id)

        first_pass = broker.process_agent_messages("worker", _handler, idempotent=True)
        second_pass = broker.process_agent_messages("worker", _handler, idempotent=True)

        self.assertEqual(first_pass, 1)
        self.assertEqual(second_pass, 0)
        self.assertEqual(handled_ids, ["stable-1"])

    def test_replay_with_metadata_variance_still_dedupes(self):
        broker = AgentMessageBroker()
        broker.send_agent_message(
            "planner",
            "worker",
            "task.created",
            "artifact://task/1",
            correlation_id="corr-a",
            message_id="  replay-1  ",
        )
        broker.send_agent_message(
            "planner",
            "worker",
            "task.updated",
            "artifact://task/1?version=2",
            correlation_id="corr-b",
            message_id="replay-1",
        )

        processed = []

        def _handler(message):
            processed.append((message.message_id, message.topic, message.payload_ref))

        count = broker.process_agent_messages("worker", _handler, idempotent=True)

        self.assertEqual(count, 1)
        self.assertEqual(processed, [("replay-1", "task.created", "artifact://task/1")])

    def test_distinct_ids_process_independently(self):
        broker = AgentMessageBroker()
        broker.send_agent_message("planner", "worker", "task", "artifact://task/1", message_id="msg-1")
        broker.send_agent_message("planner", "worker", "task", "artifact://task/2", message_id="msg-2")

        handled = []

        def _handler(message):
            handled.append(message.message_id)

        processed = broker.process_agent_messages("worker", _handler, idempotent=True)

        self.assertEqual(processed, 2)
        self.assertEqual(handled, ["msg-1", "msg-2"])

    def test_duplicate_delivery_is_safe_noop_with_stable_response(self):
        broker = AgentMessageBroker()
        broker.send_agent_message("planner", "worker", "task", "artifact://task/1", message_id="dup-1")
        broker.send_agent_message("planner", "worker", "task", "artifact://task/replay", message_id="dup-1")

        handled = []

        def _handler(message):
            handled.append(message.message_id)

        initial = broker.process_agent_messages("worker", _handler, idempotent=True)
        duplicate = broker.process_agent_messages("worker", _handler, idempotent=True)
        replay_delivery = broker.subscribe_agent_messages("worker", idempotent=True)

        self.assertEqual(initial, 1)
        self.assertEqual(duplicate, 0)
        self.assertEqual(handled, ["dup-1"])
        self.assertEqual(replay_delivery, [])


if __name__ == "__main__":
    unittest.main()

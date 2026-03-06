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

    def test_idempotent_processing_skips_duplicate_message_ids(self):
        broker = AgentMessageBroker()
        broker.send_agent_message("planner", "worker", "task", "artifact://task/1", message_id="msg-1")
        broker.send_agent_message("planner", "worker", "task", "artifact://task/1b", message_id="msg-1")

        handled_ids = []

        def _handler(message):
            handled_ids.append(message.message_id)

        processed = broker.process_agent_messages("worker", _handler, idempotent=True)

        self.assertEqual(processed, 1)
        self.assertEqual(handled_ids, ["msg-1"])


if __name__ == "__main__":
    unittest.main()

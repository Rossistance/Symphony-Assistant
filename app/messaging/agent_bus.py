"""In-memory agent-to-agent messaging primitives with traceable delivery semantics."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable
from uuid import uuid4


@dataclass(frozen=True)
class AgentMessage:
    """Compact message envelope for agent-to-agent communication."""

    message_id: str
    correlation_id: str
    from_agent: str
    to_agent: str
    topic: str
    payload_ref: str
    delivery_attempts: int = 0


@dataclass(frozen=True)
class AgentMessageEvent:
    """Telemetry event emitted when a message is sent."""

    event_type: str
    payload: dict[str, str]


@dataclass
class _QueuedMessage:
    """Internal message wrapper with ack state."""

    message: AgentMessage
    acknowledged: bool = False


@dataclass
class AgentMessageBroker:
    """Provides at-least-once delivery and idempotent processing helpers."""

    _inboxes: dict[str, list[_QueuedMessage]] = field(default_factory=lambda: defaultdict(list))
    _processed_by_agent: dict[str, set[str]] = field(default_factory=lambda: defaultdict(set))
    events: list[AgentMessageEvent] = field(default_factory=list)

    @staticmethod
    def _stable_message_id(message_id: str | None) -> str:
        """Create a canonical identifier used for deduplication."""

        if message_id is None:
            return str(uuid4())
        normalized = message_id.strip()
        if not normalized:
            return str(uuid4())
        return normalized

    def send_agent_message(
        self,
        from_agent: str,
        to_agent: str,
        topic: str,
        payload_ref: str,
        *,
        correlation_id: str | None = None,
        message_id: str | None = None,
    ) -> AgentMessage:
        """Queue a message with compact payload references and trace metadata."""

        stable_message_id = self._stable_message_id(message_id)
        envelope = AgentMessage(
            message_id=stable_message_id,
            correlation_id=correlation_id or str(uuid4()),
            from_agent=from_agent,
            to_agent=to_agent,
            topic=topic,
            payload_ref=payload_ref,
        )
        self._inboxes[to_agent].append(_QueuedMessage(message=envelope))
        self.events.append(
            AgentMessageEvent(
                event_type="agent.message.sent",
                payload={
                    "message_id": envelope.message_id,
                    "correlation_id": envelope.correlation_id,
                    "from_agent": from_agent,
                    "to_agent": to_agent,
                    "topic": topic,
                    "payload_ref": payload_ref,
                },
            )
        )
        return envelope

    def subscribe_agent_messages(
        self,
        agent_id: str,
        *,
        auto_ack: bool = False,
        idempotent: bool = True,
    ) -> list[AgentMessage]:
        """Read pending messages for an agent.

        Uses at-least-once semantics: unacknowledged messages are redelivered on
        subsequent reads. With idempotent=True, previously processed message IDs
        for this agent are skipped.
        """

        deliveries: list[AgentMessage] = []
        processed_ids = self._processed_by_agent[agent_id]
        for queued in self._inboxes[agent_id]:
            if queued.acknowledged:
                continue
            msg = queued.message
            if idempotent and msg.message_id in processed_ids:
                queued.acknowledged = True
                continue
            attempt_msg = AgentMessage(
                message_id=msg.message_id,
                correlation_id=msg.correlation_id,
                from_agent=msg.from_agent,
                to_agent=msg.to_agent,
                topic=msg.topic,
                payload_ref=msg.payload_ref,
                delivery_attempts=msg.delivery_attempts + 1,
            )
            queued.message = attempt_msg
            deliveries.append(attempt_msg)
            if auto_ack:
                queued.acknowledged = True
                processed_ids.add(msg.message_id)

        return deliveries

    def process_agent_messages(
        self,
        agent_id: str,
        handler: Callable[[AgentMessage], None],
        *,
        idempotent: bool = True,
    ) -> int:
        """Handle all deliverable messages for an agent and acknowledge on success."""

        handled = 0
        processed_ids = self._processed_by_agent[agent_id]
        for queued in self._inboxes[agent_id]:
            if queued.acknowledged:
                continue
            msg = queued.message
            if idempotent and msg.message_id in processed_ids:
                queued.acknowledged = True
                continue
            next_msg = AgentMessage(
                message_id=msg.message_id,
                correlation_id=msg.correlation_id,
                from_agent=msg.from_agent,
                to_agent=msg.to_agent,
                topic=msg.topic,
                payload_ref=msg.payload_ref,
                delivery_attempts=msg.delivery_attempts + 1,
            )
            queued.message = next_msg
            handler(next_msg)
            queued.acknowledged = True
            processed_ids.add(next_msg.message_id)
            handled += 1

        return handled

    def acknowledge_message(self, agent_id: str, message_id: str) -> bool:
        """Acknowledge one message explicitly."""

        stable_message_id = self._stable_message_id(message_id)
        for queued in self._inboxes[agent_id]:
            if queued.message.message_id != stable_message_id:
                continue
            queued.acknowledged = True
            self._processed_by_agent[agent_id].add(stable_message_id)
            return True
        return False

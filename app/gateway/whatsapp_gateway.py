"""Gateway session lifecycle manager for linked-device WhatsApp connectivity."""

from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Mapping, Protocol

from app.messaging.state_store import FileWhatsAppAuthStateStore as FileAuthStateStore
from app.messaging.state_store import WhatsAppAuthStateStore


class GatewaySessionState(str, Enum):
    """Explicit state machine for gateway-managed sessions."""

    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    RECONNECTING = "RECONNECTING"
    FAILED = "FAILED"


class ConnectionEventType(str, Enum):
    """Normalized lifecycle events for connection observability."""

    CONNECT = "gateway.connection.connect"
    DISCONNECT = "gateway.connection.disconnect"
    RECONNECT = "gateway.connection.reconnect"
    FAILURE = "gateway.connection.failure"


@dataclass(frozen=True)
class ConnectionTelemetryEvent:
    """Telemetry record emitted for connection lifecycle updates."""

    session_id: str
    state: GatewaySessionState
    previous_state: GatewaySessionState
    timestamp: float
    event_type: ConnectionEventType
    metadata: dict[str, Any] = field(default_factory=dict)


class LinkedDeviceConnector(Protocol):
    """Boundary for linked-device connect/disconnect operations."""

    def connect(self, auth_state: Mapping[str, Any]) -> dict[str, Any] | None:
        """Connect to a linked device and optionally return updated auth-state."""

    def disconnect(self) -> None:
        """Disconnect from the linked-device session."""


@dataclass(frozen=True)
class GatewaySessionSnapshot:
    """Thread-safe session snapshot for observability and callers."""

    session_id: str
    state: GatewaySessionState
    reconnect_attempts: int
    last_error: str | None


class WhatsAppGateway:
    """Lifecycle manager for linked-device auth, connectivity, and reconnect policy."""

    def __init__(
        self,
        *,
        session_id: str,
        connector: LinkedDeviceConnector,
        auth_state_store: WhatsAppAuthStateStore,
        telemetry_handler: Callable[[ConnectionTelemetryEvent], None] | None = None,
        max_reconnect_attempts: int = 5,
        base_backoff_seconds: float = 1.0,
        max_backoff_seconds: float = 30.0,
        jitter_ratio: float = 0.25,
        sleep_fn: Callable[[float], None] = time.sleep,
        random_fn: Callable[[], float] = random.random,
    ) -> None:
        self.session_id = session_id
        self._connector = connector
        self._auth_state_store = auth_state_store
        self._telemetry_handler = telemetry_handler
        self._max_reconnect_attempts = max_reconnect_attempts
        self._base_backoff_seconds = base_backoff_seconds
        self._max_backoff_seconds = max_backoff_seconds
        self._jitter_ratio = jitter_ratio
        self._sleep_fn = sleep_fn
        self._random_fn = random_fn

        self._lock = threading.RLock()
        self._state = GatewaySessionState.DISCONNECTED
        self._reconnect_attempts = 0
        self._last_error: str | None = None

    @property
    def state(self) -> GatewaySessionState:
        with self._lock:
            return self._state

    def snapshot(self) -> GatewaySessionSnapshot:
        with self._lock:
            return GatewaySessionSnapshot(
                session_id=self.session_id,
                state=self._state,
                reconnect_attempts=self._reconnect_attempts,
                last_error=self._last_error,
            )

    def connect(self) -> bool:
        with self._lock:
            if self._state in {GatewaySessionState.CONNECTING, GatewaySessionState.CONNECTED}:
                return self._state == GatewaySessionState.CONNECTED
            self._transition_to(GatewaySessionState.CONNECTING)

        try:
            auth_state = self._auth_state_store.load()
            updated_auth_state = self._connector.connect(auth_state)
            if updated_auth_state is not None:
                self._auth_state_store.save(updated_auth_state)
            with self._lock:
                self._reconnect_attempts = 0
                self._last_error = None
                self._transition_to(GatewaySessionState.CONNECTED)
            return True
        except Exception as exc:  # noqa: BLE001 - lifecycle boundary should capture and report all errors
            with self._lock:
                self._last_error = str(exc)
                self._transition_to(GatewaySessionState.RECONNECTING, error=self._last_error)
            return self._reconnect()

    def disconnect(self) -> None:
        try:
            self._connector.disconnect()
        finally:
            with self._lock:
                self._reconnect_attempts = 0
                self._transition_to(GatewaySessionState.DISCONNECTED)

    def notify_connection_lost(self, reason: str | None = None) -> bool:
        """Trigger reconnect flow when an active socket unexpectedly disconnects."""

        with self._lock:
            if self._state == GatewaySessionState.DISCONNECTED:
                return False
            self._last_error = reason
            self._transition_to(GatewaySessionState.RECONNECTING, error=reason)
        return self._reconnect()

    def _reconnect(self) -> bool:
        while True:
            with self._lock:
                if self._state == GatewaySessionState.DISCONNECTED:
                    return False
                if self._reconnect_attempts >= self._max_reconnect_attempts:
                    self._transition_to(GatewaySessionState.FAILED, error=self._last_error)
                    return False
                self._reconnect_attempts += 1
                attempt = self._reconnect_attempts
                delay = self._calculate_backoff(attempt)
                self._emit_telemetry(
                    ConnectionTelemetryEvent(
                        session_id=self.session_id,
                        state=GatewaySessionState.RECONNECTING,
                        previous_state=self._state,
                        timestamp=time.time(),
                        event_type=ConnectionEventType.RECONNECT,
                        metadata={"attempt": attempt, "delay_seconds": delay, "error": self._last_error},
                    )
                )

            self._sleep_fn(delay)

            with self._lock:
                if self._state == GatewaySessionState.DISCONNECTED:
                    return False
                self._transition_to(GatewaySessionState.CONNECTING)

            try:
                auth_state = self._auth_state_store.load()
                updated_auth_state = self._connector.connect(auth_state)
                if updated_auth_state is not None:
                    self._auth_state_store.save(updated_auth_state)
                with self._lock:
                    self._last_error = None
                    self._reconnect_attempts = 0
                    self._transition_to(GatewaySessionState.CONNECTED)
                return True
            except Exception as exc:  # noqa: BLE001 - lifecycle boundary should capture and report all errors
                with self._lock:
                    self._last_error = str(exc)
                    self._transition_to(GatewaySessionState.RECONNECTING, error=self._last_error)

    def _calculate_backoff(self, attempt: int) -> float:
        raw_delay = min(self._max_backoff_seconds, self._base_backoff_seconds * (2 ** (attempt - 1)))
        jitter_offset = raw_delay * self._jitter_ratio * (self._random_fn() - 0.5) * 2
        return max(0.0, raw_delay + jitter_offset)

    def _transition_to(self, state: GatewaySessionState, *, error: str | None = None) -> None:
        previous = self._state
        self._state = state
        event_type = self._event_type_for_state(state)
        self._emit_telemetry(
            ConnectionTelemetryEvent(
                session_id=self.session_id,
                state=state,
                previous_state=previous,
                timestamp=time.time(),
                event_type=event_type,
                metadata={"error": error} if error else {},
            )
        )

    @staticmethod
    def _event_type_for_state(state: GatewaySessionState) -> ConnectionEventType:
        if state == GatewaySessionState.CONNECTED:
            return ConnectionEventType.CONNECT
        if state == GatewaySessionState.DISCONNECTED:
            return ConnectionEventType.DISCONNECT
        if state == GatewaySessionState.FAILED:
            return ConnectionEventType.FAILURE
        return ConnectionEventType.RECONNECT

    def _emit_telemetry(self, event: ConnectionTelemetryEvent) -> None:
        if self._telemetry_handler is not None:
            self._telemetry_handler(event)

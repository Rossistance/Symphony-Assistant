"""Gateway lifecycle components."""

from app.gateway.whatsapp_gateway import (
    ConnectionEventType,
    ConnectionTelemetryEvent,
    GatewaySessionSnapshot,
    GatewaySessionState,
    WhatsAppGateway,
)
from app.messaging.state_store import FileWhatsAppAuthStateStore

FileAuthStateStore = FileWhatsAppAuthStateStore

__all__ = [
    "ConnectionEventType",
    "ConnectionTelemetryEvent",
    "FileAuthStateStore",
    "FileWhatsAppAuthStateStore",
    "GatewaySessionSnapshot",
    "GatewaySessionState",
    "WhatsAppGateway",
]

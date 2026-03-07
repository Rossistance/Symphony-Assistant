"""HTTP client boundary for messaging gateway integrations."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Protocol
from urllib import request


class GatewayClient(Protocol):
    """Adapter-facing client contract for session-scoped sends."""

    session_id: str | None

    def send(self, *, session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Submit outbound payload to the gateway."""


@dataclass(frozen=True)
class HttpGatewayClient:
    """Minimal JSON client for gateway-managed outbound messages."""

    base_url: str
    api_key: str | None
    session_id: str | None
    timeout_seconds: float = 10.0

    @classmethod
    def from_env(cls) -> "HttpGatewayClient":
        return cls(
            base_url=os.getenv("WHATSAPP_GATEWAY_URL", "http://localhost:8080"),
            api_key=os.getenv("WHATSAPP_GATEWAY_API_KEY"),
            session_id=os.getenv("WHATSAPP_GATEWAY_SESSION_ID"),
        )

    def send(self, *, session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        endpoint = f"{self.base_url.rstrip('/')}/v1/whatsapp/messages"
        body = json.dumps({"session_id": session_id, **payload}).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        req = request.Request(endpoint, data=body, method="POST", headers=headers)
        with request.urlopen(req, timeout=self.timeout_seconds) as resp:  # noqa: S310 - controlled by env
            payload = resp.read().decode("utf-8")
            return json.loads(payload)

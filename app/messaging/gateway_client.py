"""HTTP client boundary for messaging gateway integrations."""

from __future__ import annotations

import json
import os
import random
import time
from dataclasses import dataclass
from typing import Any, Callable, Protocol
from urllib import error, request


_RETRYABLE_HTTP_STATUS_CODES = {408, 409, 425, 429}


class GatewayClientError(RuntimeError):
    """Base error for messaging gateway failures."""


class GatewayTransportError(GatewayClientError):
    """Gateway request failed before receiving an HTTP response."""

    def __init__(self, message: str, *, cause: BaseException | None = None) -> None:
        super().__init__(message)
        self.cause = cause


class GatewayHTTPError(GatewayClientError):
    """Gateway request returned a non-2xx response."""

    def __init__(self, *, status_code: int, response_body: str | None = None) -> None:
        self.status_code = status_code
        self.response_body = response_body
        context = f"status={status_code}"
        if response_body:
            context += f", body={response_body}"
        super().__init__(f"Gateway HTTP error ({context})")

    @property
    def retryable(self) -> bool:
        return self.status_code >= 500 or self.status_code in _RETRYABLE_HTTP_STATUS_CODES


class GatewayRetryExhaustedError(GatewayClientError):
    """Retry budget exceeded while calling the gateway."""

    def __init__(self, *, attempts: int, last_error: GatewayClientError) -> None:
        self.attempts = attempts
        self.last_error = last_error
        super().__init__(f"Gateway retries exhausted after {attempts} attempts: {last_error}")


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
    max_attempts: int = 3
    base_backoff_seconds: float = 0.25
    max_backoff_seconds: float = 2.0
    jitter_ratio: float = 0.2
    sleep_fn: Callable[[float], None] = time.sleep
    random_fn: Callable[[], float] = random.random
    urlopen_fn: Callable[..., Any] = request.urlopen

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
        attempt = 1
        while True:
            try:
                with self.urlopen_fn(req, timeout=self.timeout_seconds) as resp:  # noqa: S310 - controlled by env
                    response_payload = resp.read().decode("utf-8")
                    return json.loads(response_payload)
            except error.HTTPError as exc:
                response_body = exc.read().decode("utf-8", errors="replace") if exc.fp else None
                mapped_error = GatewayHTTPError(status_code=exc.code, response_body=response_body)
                if attempt >= self.max_attempts or not mapped_error.retryable:
                    if attempt > 1 and mapped_error.retryable:
                        raise GatewayRetryExhaustedError(attempts=attempt, last_error=mapped_error) from exc
                    raise mapped_error from exc
            except (error.URLError, TimeoutError, OSError) as exc:
                mapped_error = GatewayTransportError(
                    f"Gateway transport error: {exc}",
                    cause=exc,
                )
                if attempt >= self.max_attempts:
                    raise GatewayRetryExhaustedError(attempts=attempt, last_error=mapped_error) from exc

            delay = self._retry_delay_seconds(attempt)
            self.sleep_fn(delay)
            attempt += 1

    def _retry_delay_seconds(self, attempt: int) -> float:
        base_delay = min(self.max_backoff_seconds, self.base_backoff_seconds * (2 ** (attempt - 1)))
        if self.jitter_ratio <= 0:
            return base_delay
        jitter = (self.random_fn() * 2 - 1) * self.jitter_ratio * base_delay
        return max(0.0, base_delay + jitter)

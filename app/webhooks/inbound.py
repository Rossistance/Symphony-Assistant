"""Normalize inbound webhook payloads while preserving provider IDs."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.messaging.base import InboundMessage


@dataclass(frozen=True)
class WebhookValidationError(Exception):
    """Raised when an inbound webhook payload fails validation."""

    details: tuple[dict[str, str], ...]

    def to_response_body(self) -> dict[str, Any]:
        return {
            "error": {
                "code": "invalid_webhook_payload",
                "message": "Webhook payload validation failed.",
                "details": list(self.details),
            }
        }


ALLOWED_FIELDS = frozenset(
    {
        "message_id",
        "MessageSid",
        "id",
        "thread_id",
        "context_id",
        "conversation_id",
        "WaId",
        "from",
        "From",
        "sender",
        "body",
        "Body",
        "text",
    }
)


def _first_present(payload: dict[str, Any], candidates: tuple[str, ...]) -> tuple[str | None, Any]:
    for field in candidates:
        if field in payload:
            return field, payload[field]
    return None, None


def _validate_payload(payload: dict[str, Any]) -> None:
    errors: list[dict[str, str]] = []

    unknown_fields = sorted(field for field in payload if field not in ALLOWED_FIELDS)
    for field in unknown_fields:
        errors.append({"field": field, "issue": "unknown_field"})

    required_fields = {
        "provider_message_id": ("message_id", "MessageSid", "id"),
        "from_user": ("from", "From", "sender"),
        "body": ("body", "Body", "text"),
    }
    optional_fields = {
        "provider_thread_id": ("thread_id", "context_id", "conversation_id", "WaId"),
    }

    for canonical_field, aliases in required_fields.items():
        alias, value = _first_present(payload, aliases)
        if alias is None:
            errors.append({"field": canonical_field, "issue": "required"})
            continue
        if not isinstance(value, str):
            errors.append({"field": alias, "issue": "must_be_string"})
            continue
        if not value.strip():
            errors.append({"field": alias, "issue": "must_be_non_empty_string"})

    for _, aliases in optional_fields.items():
        alias, value = _first_present(payload, aliases)
        if alias is None:
            continue
        if not isinstance(value, str):
            errors.append({"field": alias, "issue": "must_be_string"})

    if errors:
        ordered_errors = tuple(sorted(errors, key=lambda item: (item["field"], item["issue"])))
        raise WebhookValidationError(details=ordered_errors)



def parse_inbound_webhook(payload: dict[str, Any], *, channel: str) -> InboundMessage:
    """Parse webhook payload from a provider into normalized message shape.

    Preserves provider-specific message/thread identifiers so downstream
    reply logic can keep responses in the originating thread.
    """

    _validate_payload(payload)

    # Common aliases across Twilio/WhatsApp/iOS bridge payloads.
    provider_message_id = payload.get("message_id") or payload.get("MessageSid") or payload.get("id")
    provider_thread_id = (
        payload.get("thread_id")
        or payload.get("context_id")
        or payload.get("conversation_id")
        or payload.get("WaId")
    )
    from_user = payload.get("from") or payload.get("From") or payload.get("sender")
    body = payload.get("body") or payload.get("Body") or payload.get("text") or ""

    return InboundMessage(
        channel=channel,
        from_user=from_user,
        body=body,
        provider_message_id=provider_message_id,
        provider_thread_id=provider_thread_id,
        metadata=payload,
    )

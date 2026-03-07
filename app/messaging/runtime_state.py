"""Runtime storage wiring for persistent messaging state."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from app.messaging.state_store import SqliteMessagingStateStore


def _optional_float_from_env(name: str) -> float | None:
    value = os.getenv(name)
    if value is None:
        return None
    stripped = value.strip()
    if not stripped:
        return None
    return float(stripped)


@lru_cache(maxsize=1)
def get_runtime_state_store() -> SqliteMessagingStateStore:
    db_path = Path(os.getenv("MESSAGING_STATE_DB_PATH", ".runtime/messaging_state.db"))
    inbound_dedupe_ttl_seconds = _optional_float_from_env("INBOUND_DEDUPE_TTL_SECONDS")
    return SqliteMessagingStateStore(path=db_path, inbound_dedupe_ttl_seconds=inbound_dedupe_ttl_seconds)

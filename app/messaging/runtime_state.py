"""Runtime storage wiring for persistent messaging state."""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from app.messaging.state_store import SqliteMessagingStateStore


@lru_cache(maxsize=1)
def get_runtime_state_store() -> SqliteMessagingStateStore:
    db_path = Path(os.getenv("MESSAGING_STATE_DB_PATH", ".runtime/messaging_state.db"))
    return SqliteMessagingStateStore(path=db_path)

"""Runtime configuration for transport, channels, and model routing."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path


def _as_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _as_pipe_delimited_tuple(name: str, default: str) -> tuple[str, ...]:
    raw = os.getenv(name, default)
    return tuple(item.strip() for item in raw.split("|") if item.strip())


def _csv_tuple(name: str, default: str = "") -> tuple[str, ...]:
    raw = os.getenv(name, default)
    return tuple(item.strip() for item in raw.split(",") if item.strip())


@dataclass(frozen=True)
class MessagingConfig:
    """Feature flags and defaults for messaging adapters."""

    default_channel: str = os.getenv("DEFAULT_CHANNEL", "whatsapp")
    enable_sms_fallback: bool = _as_bool("ENABLE_SMS_FALLBACK", True)
    enable_ios_bridge: bool = _as_bool("ENABLE_IOS_BRIDGE", False)


@dataclass(frozen=True)
class ModelRouterConfig:
    """Configuration for provider discovery and routing precedence."""

    router_order: tuple[str, ...] = tuple(
        provider.strip().lower()
        for provider in os.getenv("MODEL_ROUTER_ORDER", "groq,openai,gemini,anthropic").split(",")
        if provider.strip()
    )
    max_retries: int = int(os.getenv("MODEL_PROVIDER_MAX_RETRIES", "2"))



@dataclass(frozen=True)
class DeliverablesStorageConfig:
    """Storage-specific settings for deliverable publication backends."""

    backend: str = os.getenv("DELIVERABLES_BACKEND", "in_memory").strip().lower()
    credentials_json: str = os.getenv("GOOGLE_DRIVE_CREDENTIALS_JSON", "")
    credentials_path: str = os.getenv("GOOGLE_DRIVE_CREDENTIALS_PATH", "")
    delegated_subject: str | None = (os.getenv("GOOGLE_DRIVE_DELEGATED_SUBJECT", "").strip() or None)
    use_shared_drive: bool = _as_bool("GOOGLE_DRIVE_USE_SHARED_DRIVE", False)
    drive_id: str | None = (os.getenv("GOOGLE_DRIVE_DRIVE_ID", "").strip() or None)

    @classmethod
    def from_env(cls, *, backend: str | None = None) -> "DeliverablesStorageConfig":
        return cls(
            backend=(backend if backend is not None else os.getenv("DELIVERABLES_BACKEND", "in_memory")).strip().lower(),
            credentials_json=os.getenv("GOOGLE_DRIVE_CREDENTIALS_JSON", ""),
            credentials_path=os.getenv("GOOGLE_DRIVE_CREDENTIALS_PATH", ""),
            delegated_subject=(os.getenv("GOOGLE_DRIVE_DELEGATED_SUBJECT", "").strip() or None),
            use_shared_drive=_as_bool("GOOGLE_DRIVE_USE_SHARED_DRIVE", False),
            drive_id=(os.getenv("GOOGLE_DRIVE_DRIVE_ID", "").strip() or None),
        )

    @property
    def credential_source(self) -> str:
        """Return selected source with deterministic precedence JSON > PATH > NONE."""

        if self.credentials_json.strip():
            return "GOOGLE_DRIVE_CREDENTIALS_JSON"
        if self.credentials_path.strip():
            return "GOOGLE_DRIVE_CREDENTIALS_PATH"
        return "none"

    def validate(self) -> None:
        """Fail fast on unsupported backend or malformed Google Drive settings."""

        if self.backend not in {"in_memory", "google_drive"}:
            raise ValueError("DELIVERABLES_BACKEND must be one of: in_memory, google_drive")
        if self.backend != "google_drive":
            return

        if self.credential_source == "none":
            raise ValueError(
                "google_drive backend requires GOOGLE_DRIVE_CREDENTIALS_JSON or GOOGLE_DRIVE_CREDENTIALS_PATH"
            )

        if self.credential_source == "GOOGLE_DRIVE_CREDENTIALS_PATH":
            credentials_path = Path(self.credentials_path.strip())
            if not credentials_path.exists() or not credentials_path.is_file():
                raise ValueError("GOOGLE_DRIVE_CREDENTIALS_PATH must point to an existing file")

        if self.credential_source == "GOOGLE_DRIVE_CREDENTIALS_JSON":
            self._validate_credentials_json(self.credentials_json)

        if self.use_shared_drive and not self.drive_id:
            raise ValueError("GOOGLE_DRIVE_DRIVE_ID is required when GOOGLE_DRIVE_USE_SHARED_DRIVE is enabled")

    def resolve_credentials_json(self) -> str:
        """Return normalized credentials JSON according to source precedence."""

        if self.credential_source == "GOOGLE_DRIVE_CREDENTIALS_JSON":
            raw = self.credentials_json
        elif self.credential_source == "GOOGLE_DRIVE_CREDENTIALS_PATH":
            raw = Path(self.credentials_path.strip()).read_text(encoding="utf-8")
        else:
            raise ValueError("No Google Drive credential source configured")

        self._validate_credentials_json(raw)
        return raw

    @staticmethod
    def _validate_credentials_json(raw_credentials: str) -> None:
        try:
            payload = json.loads(raw_credentials)
        except json.JSONDecodeError as exc:
            raise ValueError("Google Drive credentials payload must be valid JSON") from exc
        if not isinstance(payload, dict) or not payload.get("client_email"):
            raise ValueError("Google Drive credentials JSON must include client_email")


@dataclass(frozen=True)
class DeliverablesConfig:
    """Configuration for deliverable publication backend selection."""

    backend: str = os.getenv("DELIVERABLES_BACKEND", "in_memory").strip().lower()
    in_memory_drive_root: str = os.getenv("DELIVERABLES_IN_MEMORY_DRIVE_ROOT", "https://drive.example/files")
    google_drive_folder_id: str = os.getenv("GOOGLE_DRIVE_FOLDER_ID", "")
    google_drive_environment: str = os.getenv("DELIVERABLES_ENVIRONMENT", os.getenv("APP_ENV", "dev"))
    google_drive_allow_parent_override: bool = _as_bool("GOOGLE_DRIVE_ALLOW_PARENT_OVERRIDE", False)
    google_drive_allowed_parent_ids: tuple[str, ...] = field(
        default_factory=lambda: _csv_tuple("GOOGLE_DRIVE_ALLOWED_PARENT_IDS", "")
    )
    google_drive_share_visibility: str = "private"
    google_drive_share_expiry_hours: int | None = None
    google_drive_supports_permission_expiry: bool = False
    storage: DeliverablesStorageConfig = field(default_factory=DeliverablesStorageConfig)

    def __post_init__(self) -> None:
        if self.storage.backend != self.backend:
            object.__setattr__(self, "storage", DeliverablesStorageConfig(backend=self.backend))

    @classmethod
    def from_env(cls) -> "DeliverablesConfig":
        environment = os.getenv("DELIVERABLES_ENVIRONMENT", os.getenv("APP_ENV", "dev"))
        default_visibility = "view_only" if environment.strip().lower() in {"prod", "production", "staging"} else "private"
        visibility = os.getenv("GOOGLE_DRIVE_SHARE_VISIBILITY", default_visibility).strip().lower()
        raw_expiry_hours = os.getenv("GOOGLE_DRIVE_SHARE_EXPIRY_HOURS", "").strip()
        expiry_hours = int(raw_expiry_hours) if raw_expiry_hours else None

        config = cls(
            backend=os.getenv("DELIVERABLES_BACKEND", "in_memory").strip().lower(),
            in_memory_drive_root=os.getenv("DELIVERABLES_IN_MEMORY_DRIVE_ROOT", "https://drive.example/files"),
            google_drive_folder_id=os.getenv("GOOGLE_DRIVE_FOLDER_ID", ""),
            google_drive_environment=environment,
            google_drive_allow_parent_override=_as_bool("GOOGLE_DRIVE_ALLOW_PARENT_OVERRIDE", False),
            google_drive_allowed_parent_ids=_csv_tuple("GOOGLE_DRIVE_ALLOWED_PARENT_IDS", ""),
            google_drive_share_visibility=visibility,
            google_drive_share_expiry_hours=expiry_hours,
            google_drive_supports_permission_expiry=_as_bool("GOOGLE_DRIVE_SUPPORTS_PERMISSION_EXPIRY", False),
            storage=DeliverablesStorageConfig.from_env(backend=os.getenv("DELIVERABLES_BACKEND", "in_memory")),
        )
        config._validate_share_policy()
        config.storage.validate()
        return config

    def _validate_share_policy(self) -> None:
        if self.google_drive_share_visibility not in {"private", "view_only"}:
            raise ValueError("GOOGLE_DRIVE_SHARE_VISIBILITY must be one of: private, view_only")
        if self.google_drive_share_expiry_hours is not None and self.google_drive_share_expiry_hours <= 0:
            raise ValueError("GOOGLE_DRIVE_SHARE_EXPIRY_HOURS must be a positive integer when set")


@dataclass(frozen=True)
class OrchestratorConfig:
    """Policy controls for lead-agent autonomy and slow-mode handling."""

    slow_mode_trigger_phrases: tuple[str, ...] = field(
        default_factory=lambda: _as_pipe_delimited_tuple(
            "SLOW_MODE_TRIGGER_PHRASES",
            "this is a hard problem|let's take it slow|let’s take it slow|lets take it slow",
        )
    )


config = MessagingConfig()
model_config = ModelRouterConfig()
orchestrator_config = OrchestratorConfig()

deliverables_config = DeliverablesConfig.from_env()

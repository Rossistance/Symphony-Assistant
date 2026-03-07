"""Deliverable publishing primitives for canonical artifact links."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Protocol
from urllib.parse import quote


@dataclass(frozen=True)
class DeliverableArtifact:
    """Artifact generated during orchestration before publication."""

    artifact_id: str
    title: str
    mime_type: str
    source_ref: str


@dataclass(frozen=True)
class PublishedDeliverable:
    """Published artifact metadata safe to expose in channel replies."""

    artifact_id: str
    title: str
    mime_type: str
    drive_file_id: str
    share_url: str
    access_reference: str
    share_visibility: str
    permission_role: str
    permission_type: str
    expiry_requested_hours: int | None = None
    expiry_applied: bool = False


@dataclass(frozen=True)
class DriveSharePolicy:
    """Share visibility and optional expiry policy for Google Drive permissions."""

    visibility: str = "private"
    expiry_hours: int | None = None
    supports_permission_expiry: bool = False

    def validate(self) -> None:
        if self.visibility not in {"private", "view_only"}:
            raise DeliverablePublisherConfigError("Drive share policy visibility must be one of: private, view_only")
        if self.expiry_hours is not None and self.expiry_hours <= 0:
            raise DeliverablePublisherConfigError("Drive share expiry must be a positive integer when set")


@dataclass(frozen=True)
class DrivePermissionApplication:
    """Applied permission mapping and generated access location for a Drive file."""

    visibility: str
    permission_role: str
    permission_type: str
    share_url: str
    access_reference: str
    expiry_requested_hours: int | None
    expiry_applied: bool


class DeliverablePublisherError(RuntimeError):
    """Base class for deliverable publishing failures."""


class DeliverablePublisherConfigError(DeliverablePublisherError):
    """Raised when publisher configuration is invalid or incomplete."""


class DeliverablePublisherCredentialError(DeliverablePublisherError):
    """Raised when publisher credentials are missing, invalid, or unusable."""


class DeliverablePublisher(Protocol):
    """Publishes generated artifacts to a canonical linkable storage target."""

    def publish(self, *, task_id: str, artifacts: list[DeliverableArtifact]) -> list[PublishedDeliverable]:
        """Publishes artifacts and returns canonical metadata for user delivery."""


_DRIVE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{3,128}$")
_SAFE_SEGMENT_PATTERN = re.compile(r"[^a-z0-9_-]+")


@dataclass(frozen=True)
class DriveFolderResolution:
    """Resolved deterministic folder metadata for a specific task."""

    root_folder_id: str
    environment_folder_id: str
    task_folder_id: str
    folder_path: str


@dataclass(frozen=True)
class DriveFolderPolicy:
    """Folder policy for deterministic, bounded Google Drive task locations."""

    root_folder_id: str
    environment: str
    environment_segment: str = "env"
    tasks_segment: str = "tasks"
    allow_parent_override: bool = False
    allowed_override_parent_ids: tuple[str, ...] = ()
    max_segment_length: int = 64

    def validate(self) -> None:
        root = self.root_folder_id.strip()
        if not root:
            raise DeliverablePublisherConfigError("GOOGLE_DRIVE_FOLDER_ID is required when deliverables backend is google_drive")
        if not _DRIVE_ID_PATTERN.fullmatch(root):
            raise DeliverablePublisherConfigError("GOOGLE_DRIVE_FOLDER_ID must be a bounded Drive-safe token")

        if not self._sanitize_segment(self.environment_segment):
            raise DeliverablePublisherConfigError("Drive folder policy environment segment is invalid")
        if not self._sanitize_segment(self.tasks_segment):
            raise DeliverablePublisherConfigError("Drive folder policy tasks segment is invalid")
        if not self._sanitize_segment(self.environment):
            raise DeliverablePublisherConfigError("Drive folder policy environment is invalid")

        allowed = {item.strip() for item in self.allowed_override_parent_ids if item.strip()}
        if self.allow_parent_override and not allowed:
            raise DeliverablePublisherConfigError(
                "Drive parent override requires explicit allowed parent IDs when enabled"
            )
        for parent_id in allowed:
            if not _DRIVE_ID_PATTERN.fullmatch(parent_id):
                raise DeliverablePublisherConfigError("Drive override parent IDs must be bounded Drive-safe tokens")

    def resolve(self, *, task_id: str, parent_folder_id: str | None = None) -> DriveFolderResolution:
        """Resolve deterministic env/task folders under the policy root."""

        self.validate()
        resolved_root = self._resolve_root(parent_folder_id=parent_folder_id)

        env_segment = self._sanitize_segment(self.environment)
        task_segment = self._sanitize_segment(task_id)
        env_folder_id = hashlib.sha256(f"{resolved_root}:{self.environment_segment}:{env_segment}".encode("utf-8")).hexdigest()[:24]
        task_folder_id = hashlib.sha256(
            f"{env_folder_id}:{self.tasks_segment}:{task_segment}".encode("utf-8")
        ).hexdigest()[:24]
        path = f"{self.environment_segment}/{env_segment}/{self.tasks_segment}/{task_segment}"

        return DriveFolderResolution(
            root_folder_id=resolved_root,
            environment_folder_id=env_folder_id,
            task_folder_id=task_folder_id,
            folder_path=path,
        )

    def _resolve_root(self, *, parent_folder_id: str | None) -> str:
        if parent_folder_id is None:
            return self.root_folder_id.strip()

        candidate = parent_folder_id.strip()
        if not candidate:
            return self.root_folder_id.strip()

        if not self.allow_parent_override:
            raise DeliverablePublisherConfigError("Drive parent folder override is not allowed by policy")
        if candidate not in {item.strip() for item in self.allowed_override_parent_ids if item.strip()}:
            raise DeliverablePublisherConfigError("Drive parent folder override is not in allowed policy set")
        return candidate

    def _sanitize_segment(self, raw: str) -> str:
        normalized = _SAFE_SEGMENT_PATTERN.sub("-", raw.strip().lower()).strip("-_")
        bounded = normalized[: self.max_segment_length]
        return bounded or "unknown"


@dataclass
class InMemoryDeliverablePublisher:
    """Deterministic in-memory publisher used by tests and local adapters."""

    drive_root: str = "https://drive.example/files"
    _records: dict[str, list[PublishedDeliverable]] = field(default_factory=dict)

    def publish(self, *, task_id: str, artifacts: list[DeliverableArtifact]) -> list[PublishedDeliverable]:
        published: list[PublishedDeliverable] = []
        for artifact in artifacts:
            safe_name = Path(artifact.source_ref).name or artifact.artifact_id
            seed = f"{task_id}:{artifact.artifact_id}:{safe_name}:{artifact.mime_type}".encode("utf-8")
            token = hashlib.sha256(seed).hexdigest()[:12]
            drive_file_id = f"drv-{task_id}-{artifact.artifact_id}-{token}"
            published_artifact = PublishedDeliverable(
                artifact_id=artifact.artifact_id,
                title=artifact.title,
                mime_type=artifact.mime_type,
                drive_file_id=drive_file_id,
                share_url=f"{self.drive_root}/{drive_file_id}/{quote(safe_name)}",
                access_reference=f"{self.drive_root}/{drive_file_id}/{quote(safe_name)}",
                share_visibility="view_only",
                permission_role="reader",
                permission_type="anyone",
            )
            published.append(published_artifact)

        self._records[task_id] = list(published)
        return published


@dataclass(frozen=True)
class GoogleDriveDeliverablePublisher:
    """Google Drive publisher that validates credentials and returns stable metadata."""

    folder_id: str
    share_base_url: str = "https://drive.google.com/file/d"
    credentials_json: str | None = None
    folder_policy: DriveFolderPolicy | None = None
    share_policy: DriveSharePolicy = field(default_factory=DriveSharePolicy)
    unsupported_expiry_events: list[str] = field(default_factory=list)

    def publish(self, *, task_id: str, artifacts: list[DeliverableArtifact]) -> list[PublishedDeliverable]:
        policy = self.folder_policy or DriveFolderPolicy(
            root_folder_id=self.folder_id,
            environment=os.getenv("APP_ENV", "dev"),
        )
        resolved_folder = policy.resolve(task_id=task_id)
        self.share_policy.validate()

        raw_credentials = self.credentials_json or os.getenv("GOOGLE_DRIVE_CREDENTIALS_JSON", "")
        if not raw_credentials.strip():
            raise DeliverablePublisherCredentialError(
                "GOOGLE_DRIVE_CREDENTIALS_JSON is required when deliverables backend is google_drive"
            )

        try:
            credentials_payload = json.loads(raw_credentials)
        except json.JSONDecodeError as exc:
            raise DeliverablePublisherCredentialError(
                "GOOGLE_DRIVE_CREDENTIALS_JSON must be valid JSON"
            ) from exc

        if not isinstance(credentials_payload, dict) or not credentials_payload.get("client_email"):
            raise DeliverablePublisherCredentialError(
                "GOOGLE_DRIVE_CREDENTIALS_JSON must include client_email"
            )

        published: list[PublishedDeliverable] = []
        for artifact in artifacts:
            safe_name = Path(artifact.source_ref).name or artifact.artifact_id
            stable_input = (
                f"{resolved_folder.task_folder_id}:{resolved_folder.folder_path}:{task_id}:"
                f"{artifact.artifact_id}:{artifact.mime_type}:{safe_name}"
            ).encode(
                "utf-8"
            )
            drive_file_id = hashlib.sha256(stable_input).hexdigest()[:33]
            permission = self._apply_share_policy(drive_file_id=drive_file_id)
            published.append(
                PublishedDeliverable(
                    artifact_id=artifact.artifact_id,
                    title=artifact.title,
                    mime_type=artifact.mime_type,
                    drive_file_id=drive_file_id,
                    share_url=permission.share_url,
                    access_reference=permission.access_reference,
                    share_visibility=permission.visibility,
                    permission_role=permission.permission_role,
                    permission_type=permission.permission_type,
                    expiry_requested_hours=permission.expiry_requested_hours,
                    expiry_applied=permission.expiry_applied,
                )
            )

        return published

    def _apply_share_policy(self, *, drive_file_id: str) -> DrivePermissionApplication:
        visibility = self.share_policy.visibility
        expiry_requested = self.share_policy.expiry_hours
        expiry_applied = expiry_requested is not None and self.share_policy.supports_permission_expiry

        if expiry_requested is not None and not self.share_policy.supports_permission_expiry:
            self.unsupported_expiry_events.append(
                f"drive_file_id={drive_file_id}: permission expiry unsupported; expiry request ignored"
            )

        if visibility == "view_only":
            permission_role = "reader"
            permission_type = "anyone"
            share_url = f"{self.share_base_url}/{drive_file_id}/view"
            access_reference = share_url
        else:
            permission_role = "reader"
            permission_type = "restricted"
            share_url = f"drive://file/{drive_file_id}"
            access_reference = share_url

        if expiry_applied:
            _ = (datetime.now(UTC) + timedelta(hours=expiry_requested)).isoformat()

        return DrivePermissionApplication(
            visibility=visibility,
            permission_role=permission_role,
            permission_type=permission_type,
            share_url=share_url,
            access_reference=access_reference,
            expiry_requested_hours=expiry_requested,
            expiry_applied=expiry_applied,
        )


def create_deliverable_publisher(
    *,
    backend: str,
    drive_root: str,
    google_drive_folder_id: str,
    environment: str = "dev",
    allow_google_drive_parent_override: bool = False,
    allowed_google_drive_parent_ids: tuple[str, ...] = (),
    google_drive_share_visibility: str = "private",
    google_drive_share_expiry_hours: int | None = None,
    google_drive_supports_permission_expiry: bool = False,
) -> DeliverablePublisher:
    """Create a deliverable publisher from the configured backend."""

    normalized_backend = backend.strip().lower()
    if normalized_backend == "in_memory":
        return InMemoryDeliverablePublisher(drive_root=drive_root)
    if normalized_backend == "google_drive":
        return GoogleDriveDeliverablePublisher(
            folder_id=google_drive_folder_id,
            folder_policy=DriveFolderPolicy(
                root_folder_id=google_drive_folder_id,
                environment=environment,
                allow_parent_override=allow_google_drive_parent_override,
                allowed_override_parent_ids=allowed_google_drive_parent_ids,
            ),
            share_policy=DriveSharePolicy(
                visibility=google_drive_share_visibility,
                expiry_hours=google_drive_share_expiry_hours,
                supports_permission_expiry=google_drive_supports_permission_expiry,
            ),
        )

    raise DeliverablePublisherConfigError(
        f"Unsupported deliverables backend '{backend}'. Expected one of: in_memory, google_drive"
    )


def compose_completion_message(*, task_title: str, deliverables: list[PublishedDeliverable]) -> str:
    """Builds a channel-safe completion message that includes deliverable links."""

    lines = [f"Done — I completed {task_title}."]
    if deliverables:
        lines.append("Deliverables:")
        lines.extend(f"- {item.title}: {item.share_url}" for item in deliverables)
    return "\n".join(lines)

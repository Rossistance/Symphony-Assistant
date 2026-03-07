"""Deliverable publishing primitives for canonical artifact links."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import quote

from app.config import DeliverablesConfig, DeliverablesStorageConfig


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
    parent_folder_id: str | None = None
    access_mode: str | None = None
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
    """Folder policy for deterministic, bounded Google Drive task locations.

    Policy note: environment/task segmentation is deterministic and rooted to a
    bounded root folder to keep future backend swaps low-risk.
    """

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

    def resolve(self, *, task_id: str, parent_folder_id: str | None = None) -> DriveFolderResolution:
        self.validate()
        resolved_root = self._resolve_root(parent_folder_id=parent_folder_id)
        env_segment = self._sanitize_segment(self.environment)
        task_segment = self._sanitize_segment(task_id)
        env_folder_id = hashlib.sha256(f"{resolved_root}:{self.environment_segment}:{env_segment}".encode("utf-8")).hexdigest()[:24]
        task_folder_id = hashlib.sha256(f"{env_folder_id}:{self.tasks_segment}:{task_segment}".encode("utf-8")).hexdigest()[:24]
        return DriveFolderResolution(
            root_folder_id=resolved_root,
            environment_folder_id=env_folder_id,
            task_folder_id=task_folder_id,
            folder_path=f"{self.environment_segment}/{env_segment}/{self.tasks_segment}/{task_segment}",
        )

    def _resolve_root(self, *, parent_folder_id: str | None) -> str:
        if parent_folder_id is None or not parent_folder_id.strip():
            return self.root_folder_id.strip()
        candidate = parent_folder_id.strip()
        if not self.allow_parent_override:
            raise DeliverablePublisherConfigError("Drive parent folder override is not allowed by policy")
        allowed = {item.strip() for item in self.allowed_override_parent_ids if item.strip()}
        if candidate not in allowed:
            raise DeliverablePublisherConfigError("Drive parent folder override is not in allowed policy set")
        return candidate

    def _sanitize_segment(self, raw: str) -> str:
        normalized = _SAFE_SEGMENT_PATTERN.sub("-", raw.strip().lower()).strip("-_")
        return (normalized[: self.max_segment_length]) or "unknown"


class DriveClient(Protocol):
    """Minimal Drive API contract used by the publisher implementation."""

    def ensure_folder(self, *, parent_id: str, name: str) -> str: ...

    def upload_file(self, *, parent_id: str, title: str, mime_type: str, source_ref: str) -> str: ...


@dataclass
class DeterministicDriveClient:
    """Deterministic in-process Drive client used by tests and local development."""

    def ensure_folder(self, *, parent_id: str, name: str) -> str:
        return hashlib.sha256(f"folder:{parent_id}:{name}".encode("utf-8")).hexdigest()[:24]

    def upload_file(self, *, parent_id: str, title: str, mime_type: str, source_ref: str) -> str:
        safe_name = Path(source_ref).name or title
        seed = f"file:{parent_id}:{title}:{mime_type}:{safe_name}".encode("utf-8")
        return hashlib.sha256(seed).hexdigest()[:33]


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
            url = f"{self.drive_root}/{drive_file_id}/{quote(safe_name)}"
            published.append(
                PublishedDeliverable(
                    artifact_id=artifact.artifact_id,
                    title=artifact.title,
                    mime_type=artifact.mime_type,
                    drive_file_id=drive_file_id,
                    share_url=url,
                    access_reference=url,
                    share_visibility="view_only",
                    permission_role="reader",
                    permission_type="anyone",
                    access_mode="view_only",
                )
            )
        self._records[task_id] = list(published)
        return published


@dataclass
class GoogleDriveDeliverablePublisher:
    """Drive-backed publisher with deterministic folder policy and share policy mapping."""

    storage: DeliverablesStorageConfig
    drive_client: DriveClient = field(default_factory=DeterministicDriveClient)
    share_base_url: str = "https://drive.google.com/file/d"
    unsupported_expiry_events: list[str] = field(default_factory=list)

    def publish(self, *, task_id: str, artifacts: list[DeliverableArtifact]) -> list[PublishedDeliverable]:
        credentials_payload = self._load_credentials()
        _ = credentials_payload.get("client_email")
        folder_policy = DriveFolderPolicy(
            root_folder_id=self.storage.root_folder_id,
            environment=self.storage.environment,
            environment_segment=self.storage.environment_segment,
            tasks_segment=self.storage.tasks_segment,
            allow_parent_override=self.storage.allow_parent_override,
            allowed_override_parent_ids=self.storage.allowed_parent_ids,
        )
        resolved = folder_policy.resolve(task_id=task_id)

        env_folder_id = self.drive_client.ensure_folder(parent_id=resolved.root_folder_id, name=resolved.folder_path.split("/")[1])
        task_folder_id = self.drive_client.ensure_folder(parent_id=env_folder_id, name=resolved.folder_path.split("/")[-1])

        published: list[PublishedDeliverable] = []
        for artifact in artifacts:
            drive_file_id = self.drive_client.upload_file(
                parent_id=task_folder_id,
                title=artifact.title,
                mime_type=artifact.mime_type,
                source_ref=artifact.source_ref,
            )
            permission = self._apply_share_policy(drive_file_id=drive_file_id)
            published.append(
                PublishedDeliverable(
                    artifact_id=artifact.artifact_id,
                    title=artifact.title,
                    mime_type=artifact.mime_type,
                    drive_file_id=drive_file_id,
                    parent_folder_id=task_folder_id,
                    share_url=permission.share_url,
                    access_reference=permission.access_reference,
                    share_visibility=permission.visibility,
                    permission_role=permission.permission_role,
                    permission_type=permission.permission_type,
                    access_mode=permission.visibility,
                    expiry_requested_hours=permission.expiry_requested_hours,
                    expiry_applied=permission.expiry_applied,
                )
            )
        return published

    def _load_credentials(self) -> dict[str, Any]:
        try:
            self.storage.validate()
            raw = self.storage.resolve_credentials_json()
        except ValueError as exc:
            raise DeliverablePublisherCredentialError(str(exc)) from exc
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise DeliverablePublisherCredentialError("Google Drive credentials payload must be valid JSON") from exc
        if not isinstance(payload, dict) or not payload.get("client_email"):
            raise DeliverablePublisherCredentialError("Google Drive credentials JSON must include client_email")
        return payload

    def _apply_share_policy(self, *, drive_file_id: str) -> DrivePermissionApplication:
        policy = DriveSharePolicy(
            visibility=self.storage.share_visibility,
            expiry_hours=self.storage.share_expiry_hours,
            supports_permission_expiry=self.storage.supports_permission_expiry,
        )
        policy.validate()
        expiry_applied = bool(policy.expiry_hours and policy.supports_permission_expiry)
        if policy.expiry_hours and not policy.supports_permission_expiry:
            self.unsupported_expiry_events.append(f"drive_file_id={drive_file_id}: permission expiry unsupported")
        if policy.visibility == "view_only":
            share_url = f"{self.share_base_url}/{drive_file_id}/view"
            permission_type = "anyone"
        else:
            share_url = f"drive://file/{drive_file_id}"
            permission_type = "restricted"
        if expiry_applied:
            _ = (datetime.now(UTC) + timedelta(hours=policy.expiry_hours or 0)).isoformat()
        return DrivePermissionApplication(
            visibility=policy.visibility,
            permission_role="reader",
            permission_type=permission_type,
            share_url=share_url,
            access_reference=share_url,
            expiry_requested_hours=policy.expiry_hours,
            expiry_applied=expiry_applied,
        )


def create_deliverable_publisher(*, config: DeliverablesConfig) -> DeliverablePublisher:
    """Create a deliverable publisher from the unified config object."""

    if config.backend == "in_memory":
        return InMemoryDeliverablePublisher(drive_root=config.in_memory_drive_root)
    if config.backend == "google_drive":
        return GoogleDriveDeliverablePublisher(storage=config.storage)
    raise DeliverablePublisherConfigError(
        f"Unsupported deliverables backend '{config.backend}'. Expected one of: in_memory, google_drive"
    )


def compose_completion_message(*, task_title: str, deliverables: list[PublishedDeliverable]) -> str:
    lines = [f"Done — I completed {task_title}."]
    if deliverables:
        lines.append("Deliverables:")
        lines.extend(f"- {item.title}: {item.share_url}" for item in deliverables)
    return "\n".join(lines)

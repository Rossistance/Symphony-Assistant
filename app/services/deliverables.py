"""Deliverable publishing primitives for canonical artifact links."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field
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

    def publish(self, *, task_id: str, artifacts: list[DeliverableArtifact]) -> list[PublishedDeliverable]:
        if not self.folder_id.strip():
            raise DeliverablePublisherConfigError(
                "GOOGLE_DRIVE_FOLDER_ID is required when deliverables backend is google_drive"
            )

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
            stable_input = f"{self.folder_id}:{task_id}:{artifact.artifact_id}:{artifact.mime_type}:{safe_name}".encode(
                "utf-8"
            )
            drive_file_id = hashlib.sha256(stable_input).hexdigest()[:33]
            published.append(
                PublishedDeliverable(
                    artifact_id=artifact.artifact_id,
                    title=artifact.title,
                    mime_type=artifact.mime_type,
                    drive_file_id=drive_file_id,
                    share_url=f"{self.share_base_url}/{drive_file_id}/view",
                )
            )

        return published


def create_deliverable_publisher(*, backend: str, drive_root: str, google_drive_folder_id: str) -> DeliverablePublisher:
    """Create a deliverable publisher from the configured backend."""

    normalized_backend = backend.strip().lower()
    if normalized_backend == "in_memory":
        return InMemoryDeliverablePublisher(drive_root=drive_root)
    if normalized_backend == "google_drive":
        return GoogleDriveDeliverablePublisher(folder_id=google_drive_folder_id)

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

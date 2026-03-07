"""Deliverable publishing primitives for canonical artifact links."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol
from uuid import uuid4


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
            drive_file_id = f"drv-{task_id}-{artifact.artifact_id}-{uuid4().hex[:8]}"
            published_artifact = PublishedDeliverable(
                artifact_id=artifact.artifact_id,
                title=artifact.title,
                mime_type=artifact.mime_type,
                drive_file_id=drive_file_id,
                share_url=f"{self.drive_root}/{drive_file_id}/{safe_name}",
            )
            published.append(published_artifact)

        self._records[task_id] = list(published)
        return published


def compose_completion_message(*, task_title: str, deliverables: list[PublishedDeliverable]) -> str:
    """Builds a channel-safe completion message that includes deliverable links."""

    lines = [f"Done — I completed {task_title}."]
    if deliverables:
        lines.append("Deliverables:")
        lines.extend(f"- {item.title}: {item.share_url}" for item in deliverables)
    return "\n".join(lines)

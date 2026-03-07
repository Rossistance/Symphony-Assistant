import unittest

from app.config import DeliverablesConfig, DeliverablesStorageConfig
from app.services.deliverables import (
    DeliverableArtifact,
    DeliverablePublisherConfigError,
    DeliverablePublisherCredentialError,
    DriveFolderPolicy,
    GoogleDriveDeliverablePublisher,
    InMemoryDeliverablePublisher,
    compose_completion_message,
    create_deliverable_publisher,
)


class DriveFolderPolicyTests(unittest.TestCase):
    def test_resolve_builds_deterministic_env_task_path(self):
        policy = DriveFolderPolicy(root_folder_id="folder-123", environment="Prod Env")
        first = policy.resolve(task_id="Task #42")
        second = policy.resolve(task_id="Task #42")
        self.assertEqual(first, second)
        self.assertEqual(first.folder_path, "env/prod-env/tasks/task-42")

    def test_parent_override_requires_allowlist(self):
        policy = DriveFolderPolicy(
            root_folder_id="folder-123",
            environment="dev",
            allow_parent_override=True,
            allowed_override_parent_ids=("allowed-parent",),
        )
        with self.assertRaises(DeliverablePublisherConfigError):
            policy.resolve(task_id="task-1", parent_folder_id="not-allowed")


class DeliverablePublisherTests(unittest.TestCase):
    def test_publish_returns_drive_like_metadata(self):
        publisher = InMemoryDeliverablePublisher(drive_root="https://drive.local/files")
        published = publisher.publish(
            task_id="task-1",
            artifacts=[DeliverableArtifact("artifact-1", "Plan", "text/markdown", "tmp/plan.md")],
        )
        self.assertEqual(published[0].artifact_id, "artifact-1")
        self.assertIn("/plan.md", published[0].share_url)
        self.assertEqual(published[0].permission_type, "anyone")
        self.assertEqual(published[0].access_mode, "view_only")

    def test_google_drive_metadata_shape_consistency(self):
        storage = DeliverablesStorageConfig(
            backend="google_drive",
            credentials_json='{"client_email":"svc@example.com"}',
            root_folder_id="folder-123",
            environment="staging",
            share_visibility="view_only",
        )
        publisher = GoogleDriveDeliverablePublisher(storage=storage)
        published = publisher.publish(
            task_id="task-1",
            artifacts=[DeliverableArtifact("artifact-1", "Plan", "text/markdown", "tmp/plan.md")],
        )
        item = published[0]
        self.assertEqual(item.share_visibility, "view_only")
        self.assertEqual(item.permission_role, "reader")
        self.assertEqual(item.permission_type, "anyone")
        self.assertTrue(item.share_url.startswith("https://drive.google.com/file/d/"))
        self.assertIsNotNone(item.parent_folder_id)

    def test_google_drive_sharing_defaults_and_overrides(self):
        storage = DeliverablesStorageConfig(
            backend="google_drive",
            credentials_json='{"client_email":"svc@example.com"}',
            root_folder_id="folder-123",
            share_visibility="private",
            share_expiry_hours=12,
            supports_permission_expiry=False,
        )
        publisher = GoogleDriveDeliverablePublisher(storage=storage)
        published = publisher.publish(
            task_id="task-2",
            artifacts=[DeliverableArtifact("artifact-1", "Plan", "text/markdown", "tmp/plan.md")],
        )
        self.assertEqual(published[0].share_visibility, "private")
        self.assertFalse(published[0].expiry_applied)
        self.assertEqual(published[0].expiry_requested_hours, 12)
        self.assertEqual(len(publisher.unsupported_expiry_events), 1)

    def test_google_drive_publish_failure_behavior_missing_credentials(self):
        publisher = GoogleDriveDeliverablePublisher(
            storage=DeliverablesStorageConfig(backend="google_drive", root_folder_id="folder-123")
        )
        with self.assertRaises(DeliverablePublisherCredentialError):
            publisher.publish(
                task_id="task-1",
                artifacts=[DeliverableArtifact("artifact-1", "Plan", "text/markdown", "tmp/plan.md")],
            )

    def test_create_publisher_factory(self):
        in_memory = create_deliverable_publisher(
            config=DeliverablesConfig(backend="in_memory", in_memory_drive_root="https://drive.local/files")
        )
        self.assertIsInstance(in_memory, InMemoryDeliverablePublisher)

        drive = create_deliverable_publisher(
            config=DeliverablesConfig(
                backend="google_drive",
                google_drive_folder_id="folder-123",
                storage=DeliverablesStorageConfig(
                    backend="google_drive",
                    credentials_json='{"client_email":"svc@example.com"}',
                    root_folder_id="folder-123",
                ),
            )
        )
        self.assertIsInstance(drive, GoogleDriveDeliverablePublisher)

    def test_completion_message_contains_links(self):
        publisher = InMemoryDeliverablePublisher(drive_root="https://drive.local/files")
        published = publisher.publish(
            task_id="task-2",
            artifacts=[DeliverableArtifact("artifact-1", "Summary", "text/plain", "summary.txt")],
        )
        message = compose_completion_message(task_title="research task", deliverables=published)
        self.assertIn("Done — I completed research task.", message)
        self.assertIn("https://drive.local/files/", message)


if __name__ == "__main__":
    unittest.main()

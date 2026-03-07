import unittest

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

    def test_policy_rejects_invalid_root_folder_id(self):
        policy = DriveFolderPolicy(root_folder_id="@@", environment="dev")

        with self.assertRaises(DeliverablePublisherConfigError):
            policy.resolve(task_id="task-1")


class DeliverablePublisherTests(unittest.TestCase):
    def test_publish_returns_drive_like_metadata(self):
        publisher = InMemoryDeliverablePublisher(drive_root="https://drive.local/files")

        published = publisher.publish(
            task_id="task-1",
            artifacts=[
                DeliverableArtifact(
                    artifact_id="artifact-1",
                    title="Plan",
                    mime_type="text/markdown",
                    source_ref="tmp/plan.md",
                )
            ],
        )

        self.assertEqual(len(published), 1)
        self.assertEqual(published[0].artifact_id, "artifact-1")
        self.assertEqual(published[0].title, "Plan")
        self.assertEqual(published[0].mime_type, "text/markdown")
        self.assertTrue(published[0].drive_file_id.startswith("drv-task-1-artifact-1-"))
        self.assertIn("/plan.md", published[0].share_url)

    def test_publish_is_stable_for_same_inputs(self):
        publisher = InMemoryDeliverablePublisher(drive_root="https://drive.local/files")
        artifacts = [
            DeliverableArtifact(
                artifact_id="artifact-1",
                title="Plan",
                mime_type="text/markdown",
                source_ref="tmp/plan.md",
            )
        ]

        first = publisher.publish(task_id="task-1", artifacts=artifacts)
        second = publisher.publish(task_id="task-1", artifacts=artifacts)

        self.assertEqual(first, second)

    def test_google_drive_publish_is_stable_for_same_inputs(self):
        publisher = GoogleDriveDeliverablePublisher(
            folder_id="folder-123",
            credentials_json='{"client_email": "svc@example.com"}',
        )

        first = publisher.publish(
            task_id="task-1",
            artifacts=[
                DeliverableArtifact(
                    artifact_id="artifact-1",
                    title="Plan",
                    mime_type="text/markdown",
                    source_ref="tmp/plan.md",
                )
            ],
        )
        second = publisher.publish(
            task_id="task-1",
            artifacts=[
                DeliverableArtifact(
                    artifact_id="artifact-1",
                    title="Plan",
                    mime_type="text/markdown",
                    source_ref="tmp/plan.md",
                )
            ],
        )

        self.assertEqual(first, second)
        self.assertTrue(first[0].share_url.startswith("https://drive.google.com/file/d/"))


    def test_google_drive_uses_folder_policy_segments(self):
        publisher = GoogleDriveDeliverablePublisher(
            folder_id="folder-123",
            credentials_json='{"client_email": "svc@example.com"}',
            folder_policy=DriveFolderPolicy(root_folder_id="folder-123", environment="QA Stage"),
        )

        published = publisher.publish(
            task_id="Task #7",
            artifacts=[
                DeliverableArtifact(
                    artifact_id="artifact-1",
                    title="Plan",
                    mime_type="text/markdown",
                    source_ref="tmp/plan.md",
                )
            ],
        )

        self.assertEqual(len(published[0].drive_file_id), 33)

    def test_google_drive_publish_requires_credentials_json(self):
        publisher = GoogleDriveDeliverablePublisher(folder_id="folder-123")

        with self.assertRaises(DeliverablePublisherCredentialError):
            publisher.publish(
                task_id="task-1",
                artifacts=[
                    DeliverableArtifact(
                        artifact_id="artifact-1",
                        title="Plan",
                        mime_type="text/markdown",
                        source_ref="tmp/plan.md",
                    )
                ],
            )

    def test_google_drive_publish_requires_folder_id(self):
        publisher = GoogleDriveDeliverablePublisher(
            folder_id="",
            credentials_json='{"client_email": "svc@example.com"}',
        )

        with self.assertRaises(DeliverablePublisherConfigError):
            publisher.publish(
                task_id="task-1",
                artifacts=[
                    DeliverableArtifact(
                        artifact_id="artifact-1",
                        title="Plan",
                        mime_type="text/markdown",
                        source_ref="tmp/plan.md",
                    )
                ],
            )

    def test_create_publisher_rejects_unknown_backend(self):
        with self.assertRaises(DeliverablePublisherConfigError):
            create_deliverable_publisher(
                backend="unsupported",
                drive_root="https://drive.local/files",
                google_drive_folder_id="folder-123",
            )

    def test_completion_message_contains_links(self):
        publisher = InMemoryDeliverablePublisher(drive_root="https://drive.local/files")
        published = publisher.publish(
            task_id="task-2",
            artifacts=[
                DeliverableArtifact(
                    artifact_id="artifact-1",
                    title="Summary",
                    mime_type="text/plain",
                    source_ref="summary.txt",
                )
            ],
        )

        message = compose_completion_message(task_title="research task", deliverables=published)

        self.assertIn("Done — I completed research task.", message)
        self.assertIn("Deliverables:", message)
        self.assertIn("Summary:", message)
        self.assertIn("https://drive.local/files/", message)


if __name__ == "__main__":
    unittest.main()

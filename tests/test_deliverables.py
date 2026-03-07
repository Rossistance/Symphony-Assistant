import unittest

from app.services.deliverables import (
    DeliverableArtifact,
    InMemoryDeliverablePublisher,
    compose_completion_message,
)


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

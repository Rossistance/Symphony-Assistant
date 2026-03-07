from __future__ import annotations

import os
import unittest
from tempfile import NamedTemporaryFile
from unittest.mock import patch

from app.config import DeliverablesConfig, DeliverablesStorageConfig


class DeliverablesStorageConfigTests(unittest.TestCase):
    def test_credentials_precedence_json_over_path(self):
        with NamedTemporaryFile(mode="w", encoding="utf-8") as credentials_file:
            credentials_file.write('{"client_email":"path@example.com"}')
            credentials_file.flush()
            with patch.dict(
                os.environ,
                {
                    "DELIVERABLES_BACKEND": "google_drive",
                    "GOOGLE_DRIVE_CREDENTIALS_JSON": '{"client_email":"json@example.com"}',
                    "GOOGLE_DRIVE_CREDENTIALS_PATH": credentials_file.name,
                    "GOOGLE_DRIVE_FOLDER_ID": "folder-123",
                },
                clear=True,
            ):
                config = DeliverablesConfig.from_env()

        self.assertEqual(config.storage.credential_source, "GOOGLE_DRIVE_CREDENTIALS_JSON")
        self.assertEqual(config.storage.resolve_credentials_json(), '{"client_email":"json@example.com"}')

    def test_credentials_path_used_when_json_missing(self):
        with NamedTemporaryFile(mode="w", encoding="utf-8") as credentials_file:
            credentials_file.write('{"client_email":"dev@example.com"}')
            credentials_file.flush()
            with patch.dict(
                os.environ,
                {
                    "DELIVERABLES_BACKEND": "google_drive",
                    "GOOGLE_DRIVE_CREDENTIALS_PATH": credentials_file.name,
                    "GOOGLE_DRIVE_FOLDER_ID": "folder-123",
                },
                clear=True,
            ):
                config = DeliverablesConfig.from_env()
                self.assertEqual(config.storage.credential_source, "GOOGLE_DRIVE_CREDENTIALS_PATH")
                self.assertEqual(config.storage.resolve_credentials_json(), '{"client_email":"dev@example.com"}')

    def test_folder_and_share_policy_fields_load_from_storage(self):
        with patch.dict(
            os.environ,
            {
                "DELIVERABLES_BACKEND": "google_drive",
                "GOOGLE_DRIVE_CREDENTIALS_JSON": '{"client_email":"svc@example.com"}',
                "GOOGLE_DRIVE_FOLDER_ID": "folder-123",
                "GOOGLE_DRIVE_ENVIRONMENT_SEGMENT": "environment",
                "GOOGLE_DRIVE_TASKS_SEGMENT": "jobs",
                "GOOGLE_DRIVE_ALLOW_PARENT_OVERRIDE": "true",
                "GOOGLE_DRIVE_ALLOWED_PARENT_IDS": "a,b",
                "GOOGLE_DRIVE_SHARE_VISIBILITY": "view_only",
                "GOOGLE_DRIVE_SHARE_EXPIRY_HOURS": "24",
                "GOOGLE_DRIVE_SUPPORTS_PERMISSION_EXPIRY": "true",
            },
            clear=True,
        ):
            config = DeliverablesConfig.from_env()

        self.assertEqual(config.storage.root_folder_id, "folder-123")
        self.assertEqual(config.storage.environment_segment, "environment")
        self.assertEqual(config.storage.tasks_segment, "jobs")
        self.assertTrue(config.storage.allow_parent_override)
        self.assertEqual(config.storage.allowed_parent_ids, ("a", "b"))
        self.assertEqual(config.storage.share_visibility, "view_only")
        self.assertEqual(config.storage.share_expiry_hours, 24)
        self.assertTrue(config.storage.supports_permission_expiry)

    def test_invalid_share_visibility_fails_fast(self):
        with self.assertRaisesRegex(ValueError, "GOOGLE_DRIVE_SHARE_VISIBILITY"):
            DeliverablesStorageConfig(
                backend="google_drive",
                credentials_json='{"client_email":"svc@example.com"}',
                root_folder_id="folder-123",
                share_visibility="public",
            ).validate()

    def test_invalid_credentials_json_fails(self):
        with self.assertRaisesRegex(ValueError, "valid JSON"):
            DeliverablesStorageConfig(
                backend="google_drive",
                credentials_json="not-json",
                root_folder_id="folder-123",
            ).validate()


if __name__ == "__main__":
    unittest.main()

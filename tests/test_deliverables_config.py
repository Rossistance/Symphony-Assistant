from __future__ import annotations

import os
import unittest
from tempfile import NamedTemporaryFile
from unittest.mock import patch

from app.config import DeliverablesConfig, DeliverablesStorageConfig


class DeliverablesStorageConfigTests(unittest.TestCase):
    def test_local_prefers_inline_credentials_json(self):
        with patch.dict(
            os.environ,
            {
                "DELIVERABLES_BACKEND": "google_drive",
                "GOOGLE_DRIVE_CREDENTIALS_JSON": '{"client_email":"local@example.com"}',
            },
            clear=True,
        ):
            config = DeliverablesConfig.from_env()

        self.assertEqual(config.storage.credential_source, "GOOGLE_DRIVE_CREDENTIALS_JSON")
        self.assertEqual(config.storage.resolve_credentials_json(), '{"client_email":"local@example.com"}')

    def test_dev_uses_credentials_path_when_json_missing(self):
        with NamedTemporaryFile(mode="w", encoding="utf-8") as credentials_file:
            credentials_file.write('{"client_email":"dev@example.com"}')
            credentials_file.flush()
            with patch.dict(
                os.environ,
                {
                    "DELIVERABLES_BACKEND": "google_drive",
                    "GOOGLE_DRIVE_CREDENTIALS_PATH": credentials_file.name,
                },
                clear=True,
            ):
                config = DeliverablesConfig.from_env()
                self.assertEqual(config.storage.credential_source, "GOOGLE_DRIVE_CREDENTIALS_PATH")
                self.assertEqual(config.storage.resolve_credentials_json(), '{"client_email":"dev@example.com"}')

    def test_test_backend_in_memory_allows_no_credentials(self):
        with patch.dict(os.environ, {"DELIVERABLES_BACKEND": "in_memory"}, clear=True):
            config = DeliverablesConfig.from_env()

        self.assertEqual(config.storage.credential_source, "none")

    def test_deploy_json_wins_when_both_sources_set(self):
        with NamedTemporaryFile(mode="w", encoding="utf-8") as credentials_file:
            credentials_file.write('{"client_email":"path@example.com"}')
            credentials_file.flush()
            with patch.dict(
                os.environ,
                {
                    "DELIVERABLES_BACKEND": "google_drive",
                    "GOOGLE_DRIVE_CREDENTIALS_JSON": '{"client_email":"json@example.com"}',
                    "GOOGLE_DRIVE_CREDENTIALS_PATH": credentials_file.name,
                },
                clear=True,
            ):
                config = DeliverablesConfig.from_env()

        self.assertEqual(config.storage.credential_source, "GOOGLE_DRIVE_CREDENTIALS_JSON")
        self.assertEqual(config.storage.resolve_credentials_json(), '{"client_email":"json@example.com"}')

    def test_shared_drive_requires_drive_id_when_enabled(self):
        with self.assertRaisesRegex(ValueError, "GOOGLE_DRIVE_DRIVE_ID"):
            DeliverablesStorageConfig(
                backend="google_drive",
                credentials_json='{"client_email":"svc@example.com"}',
                use_shared_drive=True,
            ).validate()


    def test_share_policy_defaults_to_private_for_non_production_environments(self):
        with patch.dict(
            os.environ,
            {
                "DELIVERABLES_BACKEND": "in_memory",
                "DELIVERABLES_ENVIRONMENT": "dev",
            },
            clear=True,
        ):
            config = DeliverablesConfig.from_env()

        self.assertEqual(config.google_drive_share_visibility, "private")
        self.assertIsNone(config.google_drive_share_expiry_hours)
        self.assertFalse(config.google_drive_supports_permission_expiry)

    def test_share_policy_defaults_to_view_only_for_staging(self):
        with patch.dict(
            os.environ,
            {
                "DELIVERABLES_BACKEND": "in_memory",
                "DELIVERABLES_ENVIRONMENT": "staging",
            },
            clear=True,
        ):
            config = DeliverablesConfig.from_env()

        self.assertEqual(config.google_drive_share_visibility, "view_only")

    def test_share_policy_overrides_are_loaded(self):
        with patch.dict(
            os.environ,
            {
                "DELIVERABLES_BACKEND": "in_memory",
                "GOOGLE_DRIVE_SHARE_VISIBILITY": "private",
                "GOOGLE_DRIVE_SHARE_EXPIRY_HOURS": "24",
                "GOOGLE_DRIVE_SUPPORTS_PERMISSION_EXPIRY": "true",
            },
            clear=True,
        ):
            config = DeliverablesConfig.from_env()

        self.assertEqual(config.google_drive_share_visibility, "private")
        self.assertEqual(config.google_drive_share_expiry_hours, 24)
        self.assertTrue(config.google_drive_supports_permission_expiry)

    def test_invalid_backend_fails_fast(self):
        with self.assertRaisesRegex(ValueError, "DELIVERABLES_BACKEND"):
            DeliverablesStorageConfig(backend="something_else").validate()


    def test_folder_policy_env_and_parent_overrides_are_loaded(self):
        with patch.dict(
            os.environ,
            {
                "DELIVERABLES_BACKEND": "google_drive",
                "GOOGLE_DRIVE_CREDENTIALS_JSON": '{"client_email":"svc@example.com"}',
                "DELIVERABLES_ENVIRONMENT": "staging",
                "GOOGLE_DRIVE_ALLOW_PARENT_OVERRIDE": "true",
                "GOOGLE_DRIVE_ALLOWED_PARENT_IDS": "rootA,rootB",
            },
            clear=True,
        ):
            config = DeliverablesConfig.from_env()

        self.assertEqual(config.google_drive_environment, "staging")
        self.assertTrue(config.google_drive_allow_parent_override)
        self.assertEqual(config.google_drive_allowed_parent_ids, ("rootA", "rootB"))


if __name__ == "__main__":
    unittest.main()

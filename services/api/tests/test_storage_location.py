import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException

from app.models.settings import ApplicationSettingsValues
from app.services.application_settings import _prepare_storage_locations, effective_storage_roots
from app.services.storage import get_upload_directory, resolve_storage_file


class StorageLocationTests(unittest.TestCase):
    def test_new_absolute_location_is_created_and_previous_location_is_retained(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            base = Path(temporary_directory)
            environment_root = base / "environment-storage"
            old_root = base / "old-storage"
            new_root = base / "new-storage"
            environment_root.mkdir()
            old_root.mkdir()
            values = ApplicationSettingsValues()
            values.storage_retention.storage_location = str(new_root)
            current = {"storage_retention": {"storage_location": str(old_root)}}

            with patch(
                "app.services.application_settings.get_settings",
                return_value=SimpleNamespace(storage_root=str(environment_root)),
            ):
                _prepare_storage_locations(values, current)

            self.assertTrue(new_root.is_dir())
            self.assertEqual(values.storage_retention.storage_location, str(new_root.resolve()))
            self.assertEqual(values.storage_retention.previous_storage_locations, [str(old_root.resolve())])

    def test_relative_and_filesystem_root_locations_are_rejected(self):
        values = ApplicationSettingsValues()
        values.storage_retention.storage_location = "relative/storage"
        with self.assertRaisesRegex(HTTPException, "absolute path"):
            _prepare_storage_locations(values, None)

        values.storage_retention.storage_location = str(Path("/").resolve())
        with self.assertRaisesRegex(HTTPException, "Filesystem root"):
            _prepare_storage_locations(values, None)

    def test_client_cannot_inject_an_unregistered_previous_root(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            base = Path(temporary_directory)
            environment_root = base / "environment"
            new_root = base / "new"
            injected_root = base / "injected"
            environment_root.mkdir()
            values = ApplicationSettingsValues()
            values.storage_retention.storage_location = str(new_root)
            values.storage_retention.previous_storage_locations = [str(injected_root)]

            with patch(
                "app.services.application_settings.get_settings",
                return_value=SimpleNamespace(storage_root=str(environment_root)),
            ):
                _prepare_storage_locations(values, {"storage_retention": {}})

            self.assertEqual(values.storage_retention.previous_storage_locations, [])

    def test_effective_roots_include_active_previous_and_environment_locations(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            base = Path(temporary_directory)
            settings = SimpleNamespace(
                storage_location=str(base / "active"),
                previous_storage_locations=[str(base / "previous")],
            )
            with patch(
                "app.services.application_settings.get_settings",
                return_value=SimpleNamespace(storage_root=str(base / "environment")),
            ):
                roots = effective_storage_roots(settings)

            self.assertEqual(roots, (
                (base / "active").resolve(),
                (base / "previous").resolve(),
                (base / "environment").resolve(),
            ))

    def test_new_uploads_use_active_root_and_legacy_files_remain_readable(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            base = Path(temporary_directory)
            active_root = base / "active"
            previous_root = base / "previous"
            previous_root.mkdir()
            legacy_file = previous_root / "uploads" / "legacy.wav"
            legacy_file.parent.mkdir()
            legacy_file.write_bytes(b"legacy")

            with patch("app.services.storage.effective_storage_root", return_value=active_root):
                self.assertEqual(get_upload_directory(), active_root / "uploads")
            with patch(
                "app.services.storage.effective_storage_roots",
                return_value=(active_root, previous_root),
            ):
                self.assertEqual(resolve_storage_file(legacy_file), legacy_file.resolve())


if __name__ == "__main__":
    unittest.main()

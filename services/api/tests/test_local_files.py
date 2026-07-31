import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from bson import ObjectId
from fastapi import HTTPException

from app.services.media_files import delete_local_file, list_local_files


def database_with(media_collection, jobs_collection, subtitle_collection):
    database = MagicMock()
    database.__getitem__.side_effect = lambda name: {
        "media_files": media_collection,
        "transcription_jobs": jobs_collection,
        "subtitle_projects": subtitle_collection,
    }[name]
    return database


class LocalFilesTests(unittest.TestCase):
    def test_list_exposes_usage_and_protects_active_job_file(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "meeting.wav"
            path.write_bytes(b"RIFF" + b"0" * 12)
            media_id = ObjectId()
            media = {
                "_id": media_id,
                "original_name": "meeting.wav",
                "stored_path": str(path),
                "media_type": "audio",
                "content_type": "audio/wav",
                "created_at": datetime.now(timezone.utc),
            }
            media_collection = MagicMock()
            media_collection.find.return_value.sort.return_value.limit.return_value = [media]
            jobs_collection = MagicMock()
            jobs_collection.count_documents.side_effect = [2, 1]
            subtitle_collection = MagicMock()
            subtitle_collection.count_documents.return_value = 0
            database = database_with(media_collection, jobs_collection, subtitle_collection)

            with patch("app.services.media_files.get_database", return_value=database), patch(
                "app.services.media_files.resolve_storage_file", return_value=path
            ):
                [result] = list_local_files()

            self.assertEqual(result.original_name, "meeting.wav")
            self.assertEqual(result.file_size, 16)
            self.assertEqual(result.job_count, 2)
            self.assertEqual(result.active_job_count, 1)
            self.assertFalse(result.deletable)
            self.assertIn("queued or processing", result.protection_reason)

    def test_delete_removes_binary_but_retains_media_metadata(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "completed.wav"
            path.write_bytes(b"transcript source")
            media_id = ObjectId()
            media = {
                "_id": media_id,
                "original_name": "completed.wav",
                "stored_path": str(path),
            }
            media_collection = MagicMock()
            media_collection.find_one.return_value = media
            jobs_collection = MagicMock()
            jobs_collection.count_documents.side_effect = [1, 0]
            subtitle_collection = MagicMock()
            subtitle_collection.count_documents.return_value = 0
            database = database_with(media_collection, jobs_collection, subtitle_collection)

            with patch("app.services.media_files.get_database", return_value=database), patch(
                "app.services.media_files.resolve_storage_file", return_value=path
            ):
                result = delete_local_file(str(media_id))

            self.assertEqual(result.bytes_deleted, len(b"transcript source"))
            self.assertFalse(path.exists())
            media_collection.delete_one.assert_not_called()
            update = media_collection.update_one.call_args.args[1]
            self.assertIsNone(update["$set"]["stored_path"])
            self.assertIn("local_file_deleted_at", update["$set"])

    def test_delete_rejects_file_used_by_active_job(self):
        media_id = ObjectId()
        media_collection = MagicMock()
        media_collection.find_one.return_value = {"_id": media_id, "stored_path": "/storage/active.wav"}
        jobs_collection = MagicMock()
        jobs_collection.count_documents.side_effect = [1, 1]
        subtitle_collection = MagicMock()
        subtitle_collection.count_documents.return_value = 0
        database = database_with(media_collection, jobs_collection, subtitle_collection)

        with patch("app.services.media_files.get_database", return_value=database):
            with self.assertRaisesRegex(HTTPException, "queued or processing") as raised:
                delete_local_file(str(media_id))

        self.assertEqual(raised.exception.status_code, 409)


if __name__ == "__main__":
    unittest.main()

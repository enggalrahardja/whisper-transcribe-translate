import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi import HTTPException

from app.services.live_sessions import COLLECTION_NAME, delete_live_session, elapsed_session_seconds
from app.services.pipeline_persistence import COLLECTIONS as PIPELINE_COLLECTIONS


class FakeCollection:
    def __init__(self, documents=None):
        self.documents = list(documents or [])
        self.deleted_queries = []

    @staticmethod
    def _matches(document, query):
        for key, expected in query.items():
            actual = document.get(key)
            if isinstance(expected, dict) and "$in" in expected:
                if actual not in expected["$in"]:
                    return False
            elif actual != expected:
                return False
        return True

    def find_one_and_delete(self, query):
        for index, document in enumerate(self.documents):
            if self._matches(document, query):
                return self.documents.pop(index)
        return None

    def find_one(self, query, _projection=None):
        return next((item for item in self.documents if self._matches(item, query)), None)

    def delete_many(self, query):
        self.deleted_queries.append(query)
        before = len(self.documents)
        self.documents = [item for item in self.documents if not self._matches(item, query)]
        return type("DeleteResult", (), {"deleted_count": before - len(self.documents)})()


class FakeDatabase(dict):
    def __getitem__(self, key):
        return self.setdefault(key, FakeCollection())


class LiveSessionHistoryTests(unittest.TestCase):
    def test_elapsed_session_time_accepts_mongodb_naive_datetime(self):
        now = datetime(2026, 7, 31, 8, 0, tzinfo=timezone.utc)
        mongodb_started_at = datetime(2026, 7, 31, 7, 59, 30)
        aware_started_at = now - timedelta(seconds=30)

        self.assertEqual(elapsed_session_seconds(mongodb_started_at, now=now), 30)
        self.assertEqual(elapsed_session_seconds(aware_started_at, now=now), 30)

    def test_completed_session_delete_cascades_pipeline_records(self):
        database = FakeDatabase({
            COLLECTION_NAME: FakeCollection([{"session_id": "done", "status": "completed"}]),
        })
        for name in PIPELINE_COLLECTIONS.values():
            database[name] = FakeCollection([{"sessionId": "done"}, {"sessionId": "keep"}])

        with patch("app.services.live_sessions.get_database", return_value=database):
            self.assertTrue(delete_live_session("done"))

        self.assertEqual(database[COLLECTION_NAME].documents, [])
        for name in PIPELINE_COLLECTIONS.values():
            self.assertEqual(database[name].documents, [{"sessionId": "keep"}])

    def test_active_session_cannot_be_deleted(self):
        database = FakeDatabase({
            COLLECTION_NAME: FakeCollection([{"session_id": "active", "status": "active"}]),
        })
        with patch("app.services.live_sessions.get_database", return_value=database), self.assertRaises(HTTPException) as context:
            delete_live_session("active")
        self.assertEqual(context.exception.status_code, 409)


if __name__ == "__main__":
    unittest.main()

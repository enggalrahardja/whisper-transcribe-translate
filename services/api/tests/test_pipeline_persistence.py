import unittest

from app.config import Settings
from app.services.pipeline_persistence import (
    InMemoryPipelineRepository, PipelinePersistenceService, redact_secrets,
    versioned,
)


class RepositoryTests(unittest.TestCase):
    def setUp(self): self.repo = InMemoryPipelineRepository()

    def test_session_create_update_end_and_snapshots(self):
        self.repo.write_session(versioned({"sessionId": "s", "status": "active", "sourceType": "microphone", "featureFlags": {"vad": True}, "configuration": {"model": "base"}, "hardware": {"device": "cpu"}}))
        self.repo.write_session(versioned({"sessionId": "s", "status": "completed", "endedAt": "now"}))
        self.assertEqual(self.repo.sessions["s"]["status"], "completed")
        self.assertEqual(self.repo.sessions["s"]["configuration"]["model"], "base")

    def test_unique_session_segment_and_duplicate_idempotent(self):
        segment = versioned({"sessionId": "s", "segmentId": "a", "sequenceStart": 1, "sequenceEnd": 2})
        self.assertTrue(self.repo.write_segment(segment))
        self.assertFalse(self.repo.write_segment(segment))
        with self.assertRaises(ValueError): self.repo.write_segment({**segment, "sequenceEnd": 3})

    def test_transcript_monotonic_duplicate_immutable_and_accurate_final(self):
        first = versioned({"sessionId": "s", "segmentId": "a", "revision": 3, "state": "final", "sourceType": "live", "rawText": "raw", "glossaryCorrectedText": "text", "postProcessedText": None})
        self.assertTrue(self.repo.write_transcript(first))
        self.assertFalse(self.repo.write_transcript(first))
        with self.assertRaises(ValueError): self.repo.write_transcript({**first, "rawText": "changed"})
        accurate = versioned({**first, "revision": 4, "sourceType": "accurate_final", "rawText": "accurate"})
        self.assertTrue(self.repo.write_transcript(accurate))
        with self.assertRaises(ValueError): self.repo.write_transcript(versioned({**first, "revision": 2}))

    def test_translation_revision(self):
        value = versioned({"sessionId": "s", "segmentId": "a", "revision": 1, "status": "completed", "rawTranslation": "raw", "correctedTranslation": "corrected"})
        self.assertTrue(self.repo.write_translation(value))
        self.assertFalse(self.repo.write_translation(value))

    def test_speaker_assignment_rename_does_not_touch_transcript(self):
        transcript = versioned({"sessionId": "s", "segmentId": "a", "revision": 3, "state": "final", "rawText": "hello"})
        self.repo.write_transcript(transcript)
        self.repo.write_speaker(versioned({"sessionId": "s", "segmentId": "a", "speakerId": "speaker-1", "speakerLabel": "Speaker 1", "confidence": .9}))
        self.assertEqual(self.repo.rename_speaker("s", "speaker-1", "Alice"), 1)
        self.assertEqual(self.repo.speakers[("s", "a")]["speakerLabel"], "Alice")
        self.assertEqual(self.repo.transcripts[("s", "a", 3)]["rawText"], "hello")

    def test_restore_and_session_isolation(self):
        for session in ("a", "b"):
            self.repo.write_session(versioned({"sessionId": session, "status": "active"}))
            self.repo.write_segment(versioned({"sessionId": session, "segmentId": "one"}))
        restored = self.repo.restore("a")
        self.assertEqual(restored["session"]["sessionId"], "a")
        self.assertEqual({item["sessionId"] for item in restored["segments"]}, {"a"})

    def test_secret_redaction(self):
        value = redact_secrets({"apiKey": "secret", "nested": {"password": "secret", "model": "base"}})
        self.assertEqual(value["apiKey"], "[REDACTED]")
        self.assertEqual(value["nested"]["password"], "[REDACTED]")
        self.assertEqual(value["nested"]["model"], "base")


class FailingRepository(InMemoryPipelineRepository):
    def __init__(self, failures=1): super().__init__(); self.failures = failures
    def write_session(self, value):
        if self.failures:
            self.failures -= 1
            raise OSError("database unavailable")
        return super().write_session(value)


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_failure_retry_is_bounded_and_ingestion_submit_nonblocking(self):
        repo = FailingRepository(1)
        service = PipelinePersistenceService(repo, max_retries=1)
        await service.start()
        self.assertTrue(service.submit("session", {"sessionId": "s", "status": "active"}))
        await service.join()
        self.assertEqual(service.metrics()["retries"], 1)
        self.assertEqual(service.metrics()["successful_writes"], 1)
        await service.close()

    async def test_permanent_failure_marks_degraded_without_raising(self):
        service = PipelinePersistenceService(FailingRepository(5), max_retries=1)
        await service.start()
        self.assertTrue(service.submit("session", {"sessionId": "s"}))
        await service.join()
        self.assertEqual(service.metrics()["degraded_sessions"], 1)
        await service.close()

    async def test_restore_metrics(self):
        repo = InMemoryPipelineRepository()
        repo.write_session(versioned({"sessionId": "s", "status": "active"}))
        service = PipelinePersistenceService(repo)
        restored = await service.restore("s")
        self.assertEqual(restored["session"]["sessionId"], "s")
        self.assertEqual(service.metrics()["restore_count"], 1)

    def test_feature_flag_off_legacy_compatibility(self):
        settings = Settings()
        self.assertFalse(settings.live_pipeline_persistence_enabled)
        from app.models.live import CreateLiveSessionRequest
        self.assertEqual(CreateLiveSessionRequest().model, "base")


if __name__ == "__main__": unittest.main()

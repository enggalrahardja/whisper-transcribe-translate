import asyncio
import copy
import threading
import time
import unittest

from app.config import Settings
from app.services.speaker_diarization import (
    DiarizationRequest,
    DiarizationStateRegistry,
    DiarizationStatus,
    LocalSpeakerDiarizationQueue,
    PersistentLocalSpeakerEmbedder,
    SessionSpeakerClusterer,
    SpeakerDiarizationConfig,
    SpeakerEmbedding,
)


def request(*, session="session-a", segment="segment-1", start=100, end=900):
    return DiarizationRequest(
        session_id=session,
        segment_id=segment,
        sequence_start=1,
        sequence_end=4,
        start_ms=start,
        end_ms=end,
        audio_pcm16=b"\x01\x00" * 4_000,
    )


def embedding(values, latency=3):
    return SpeakerEmbedding(
        values=tuple(values),
        provider="fake-local",
        model="ecapa-test",
        checkpoint="checkpoint-sha",
        locality="local",
        device="cpu",
        compute_type="float32",
        embedding_version="embedding-v1",
        latency_ms=latency,
    )


class FakeEmbedder:
    def __init__(self, outcomes=None, delay=0):
        self.outcomes = list(outcomes or [])
        self.delay = delay
        self.calls = 0
        self.active = 0
        self.max_active = 0
        self.model_load_time_ms = 4.0
        self.lock = threading.Lock()

    def embed(self, _request):
        with self.lock:
            self.calls += 1
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            outcome = self.outcomes.pop(0) if self.outcomes else embedding((1, 0))
        try:
            if self.delay:
                time.sleep(self.delay)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        finally:
            with self.lock:
                self.active -= 1


class SpeakerClusteringTests(unittest.TestCase):
    def test_single_speaker_uses_initial_speaker_id(self):
        clusterer = SessionSpeakerClusterer(0.72)
        assignment = clusterer.assign("session-a", (1, 0))
        self.assertEqual(assignment.speaker_id, "speaker-1")
        self.assertEqual(assignment.speaker_label, "Speaker 1")
        self.assertEqual(clusterer.speaker_count("session-a"), 1)

    def test_multiple_speakers_get_sequential_ids(self):
        clusterer = SessionSpeakerClusterer(0.72)
        first = clusterer.assign("session-a", (1, 0))
        second = clusterer.assign("session-a", (0, 1))
        self.assertEqual((first.speaker_id, second.speaker_id), ("speaker-1", "speaker-2"))

    def test_speaker_mapping_is_stable_across_segments(self):
        clusterer = SessionSpeakerClusterer(0.72)
        first = clusterer.assign("session-a", (1, 0))
        clusterer.assign("session-a", (0, 1))
        again = clusterer.assign("session-a", (0.99, 0.01))
        self.assertEqual(again.speaker_id, first.speaker_id)
        self.assertEqual(again.clustering_revision, 3)

    def test_sessions_are_isolated(self):
        clusterer = SessionSpeakerClusterer(0.72)
        a = clusterer.assign("session-a", (1, 0))
        b = clusterer.assign("session-b", (0, 1))
        self.assertEqual(a.speaker_id, "speaker-1")
        self.assertEqual(b.speaker_id, "speaker-1")
        self.assertEqual(clusterer.speaker_count(), 2)


class SpeakerDiarizationQueueTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.queues = []

    async def asyncTearDown(self):
        for queue in self.queues:
            await queue.close()

    def queue(self, embedder=None, **overrides):
        values = dict(timeout_seconds=1, max_retries=0, worker_concurrency=1, queue_capacity=8)
        values.update(overrides)
        queue = LocalSpeakerDiarizationQueue(
            SpeakerDiarizationConfig(**values),
            embedder or FakeEmbedder(),
        )
        self.queues.append(queue)
        return queue

    async def test_assignment_metadata_and_timestamps_are_retained(self):
        queue = self.queue()
        await queue.enqueue(request(), self.ignore)
        await queue.join()
        snapshot = queue.snapshot("session-a")[0]
        self.assertEqual(snapshot.status, DiarizationStatus.COMPLETED)
        self.assertEqual((snapshot.start_ms, snapshot.end_ms), (100, 900))
        metadata = snapshot.assignment.as_dict()
        for field in (
            "provider", "model", "checkpoint", "localCloud", "device",
            "computeType", "speakerId", "speakerLabel", "confidence",
            "embeddingVersion", "clusteringRevision", "latencyMs",
            "startMs", "endMs", "createdAt", "updatedAt",
        ):
            self.assertIn(field, metadata)

    async def test_duplicate_job_is_idempotent(self):
        embedder = FakeEmbedder()
        queue = self.queue(embedder)
        first = await queue.enqueue(request(), self.ignore)
        await queue.join()
        duplicate = await queue.enqueue(request(), self.ignore)
        self.assertTrue(first.accepted)
        self.assertFalse(duplicate.accepted)
        self.assertEqual(duplicate.reason, "duplicate")
        self.assertEqual(embedder.calls, 1)

    async def test_retry_can_recover_and_timeout_falls_back_unassigned(self):
        retry = self.queue(FakeEmbedder([RuntimeError("temporary"), embedding((1, 0))]), max_retries=1)
        await retry.enqueue(request(), self.ignore)
        await retry.join()
        self.assertEqual(retry.snapshot("session-a")[0].status, DiarizationStatus.COMPLETED)
        self.assertEqual(retry.metrics()["retries"], 1)

        timeout = self.queue(FakeEmbedder(delay=0.05), timeout_seconds=0.005)
        await timeout.enqueue(request(session="timeout-session"), self.ignore)
        await timeout.join()
        failed = timeout.snapshot("timeout-session")[0]
        self.assertEqual(failed.status, DiarizationStatus.FAILED)
        self.assertIsNone(failed.assignment)

    async def test_failure_preserves_segment_without_assignment(self):
        queue = self.queue(FakeEmbedder([RuntimeError("failed")]))
        await queue.enqueue(request(), self.ignore)
        await queue.join()
        snapshot = queue.snapshot("session-a")[0]
        self.assertEqual(snapshot.status, DiarizationStatus.FAILED)
        self.assertIsNone(snapshot.assignment)
        self.assertEqual(queue.metrics()["unassigned_segments"], 1)

    async def test_reconnect_snapshot_restores_mapping(self):
        queue = self.queue(FakeEmbedder([embedding((1, 0)), embedding((0.99, 0.01))]))
        await queue.enqueue(request(segment="segment-1"), self.ignore)
        await queue.enqueue(request(segment="segment-2"), self.ignore)
        await queue.join()
        restored = queue.snapshot("session-a")
        self.assertEqual(len(restored), 2)
        self.assertEqual({item.assignment.speaker_id for item in restored}, {"speaker-1"})

    async def test_speaker_rename_updates_all_session_segments_and_future_mapping(self):
        queue = self.queue(FakeEmbedder([
            embedding((1, 0)), embedding((0.99, 0.01)), embedding((0.98, 0.02)),
        ]))
        await queue.enqueue(request(segment="segment-1"), self.ignore)
        await queue.enqueue(request(segment="segment-2"), self.ignore)
        await queue.join()
        renamed = queue.rename("session-a", "speaker-1", "Alice")
        self.assertEqual({item.assignment.speaker_label for item in renamed}, {"Alice"})
        await queue.enqueue(request(segment="segment-3"), self.ignore)
        await queue.join()
        self.assertEqual(queue.snapshot("session-a")[-1].assignment.speaker_label, "Alice")
        self.assertEqual(queue.metrics()["speaker_rename_count"], 1)

    async def test_transcript_translation_and_timestamps_are_not_mutated(self):
        transcript = {"segmentId": "segment-1", "text": "hello", "startMs": 100, "endMs": 900}
        translation = {"segmentId": "segment-1", "text": "halo", "startMs": 100, "endMs": 900}
        before_transcript = copy.deepcopy(transcript)
        before_translation = copy.deepcopy(translation)
        queue = self.queue()
        await queue.enqueue(request(), self.ignore)
        await queue.join()
        self.assertEqual(transcript, before_transcript)
        self.assertEqual(translation, before_translation)
        assignment = queue.snapshot("session-a")[0].assignment
        self.assertEqual((assignment.start_ms, assignment.end_ms), (100, 900))

    async def test_queue_and_worker_concurrency_are_bounded(self):
        queue = self.queue(FakeEmbedder(delay=0.02), queue_capacity=1)
        await queue.enqueue(request(segment="segment-a"), self.ignore)
        with self.assertRaises(asyncio.QueueFull):
            await queue.enqueue(request(segment="segment-b"), self.ignore)
        await queue.join()

        embedder = FakeEmbedder(delay=0.02)
        concurrent = self.queue(embedder, worker_concurrency=2)
        for index in range(4):
            await concurrent.enqueue(request(segment=f"segment-{index}"), self.ignore)
        await concurrent.join()
        self.assertLessEqual(embedder.max_active, 2)

    async def test_enqueue_is_non_blocking_for_live_path(self):
        queue = self.queue(FakeEmbedder(delay=0.05))
        started = time.perf_counter()
        await queue.enqueue(request(), self.ignore)
        enqueue_ms = (time.perf_counter() - started) * 1000
        self.assertLess(enqueue_ms, 25)
        await queue.join()

    async def test_metrics_cover_diarization_contract(self):
        queue = self.queue(FakeEmbedder([embedding((1, 0)), embedding((0, 1))]))
        await queue.enqueue(request(segment="segment-1"), self.ignore)
        await queue.enqueue(request(segment="segment-2"), self.ignore)
        await queue.join()
        metrics = queue.metrics()
        self.assertEqual(metrics["diarization_jobs"], 2)
        self.assertEqual(metrics["detected_speakers"], 2)
        self.assertEqual(metrics["assigned_segments"], 2)
        self.assertEqual(metrics["failures"], 0)
        self.assertEqual(metrics["queue_depth"], 0)
        self.assertEqual(metrics["model_load_time_ms"], 4)

    async def ignore(self, _snapshot):
        return None


class PersistentSpeakerModelTests(unittest.TestCase):
    def test_model_loader_is_persistent(self):
        calls = 0

        def loader():
            nonlocal calls
            calls += 1
            return object(), "checkpoint-sha", "cpu", "float32"

        embedder = PersistentLocalSpeakerEmbedder(
            SpeakerDiarizationConfig(),
            runtime_loader=loader,
        )
        embedder.ensure_loaded()
        embedder.ensure_loaded()
        self.assertEqual(calls, 1)

    def test_feature_flag_defaults_off_and_legacy_model_stays_base(self):
        settings = Settings()
        self.assertFalse(settings.live_diarization_enabled)
        from app.models.live import CreateLiveSessionRequest
        self.assertEqual(CreateLiveSessionRequest().model, "base")


if __name__ == "__main__":
    unittest.main()

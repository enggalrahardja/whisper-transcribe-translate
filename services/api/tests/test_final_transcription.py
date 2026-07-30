import asyncio
import threading
import time
import unittest
from importlib import import_module
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.config import Settings
from app.services.final_transcription import (
    FinalJobStatus,
    FinalJobSnapshot,
    FinalModelMetadata,
    FinalTranscriptionConfig,
    FinalTranscriptionRequest,
    FinalTranscriptionResult,
    FinalTranscriptionTimeout,
    LocalFinalTranscriptionQueue,
    PersistentLocalFinalTranscriber,
)
from app.services.live_transcript_state import (
    LiveTranscriptStateRegistry,
    LiveTranscriptUpdate,
    TranscriptState,
)


def request(session_id="session-a", segment_id="segment-1"):
    return FinalTranscriptionRequest(
        session_id=session_id,
        segment_id=segment_id,
        sequence_start=1,
        sequence_end=3,
        start_ms=200,
        end_ms=800,
        language="en",
        audio_wav=b"RIFF complete-vad-audio",
    )


def result(text="accurate text"):
    return FinalTranscriptionResult(
        text=text,
        metadata=FinalModelMetadata(
            model="base",
            checkpoint_path="C:/models/base.pt",
            checkpoint_sha256="a" * 64,
            device="cpu",
            compute_type="float32",
            language="en",
            beam_size=5,
            timestamps=({"startMs": 200, "endMs": 800, "text": text},),
            latency_ms=42,
        ),
    )


class FakeTranscriber:
    def __init__(self, outcomes=None, delay=0.0):
        self.outcomes = list(outcomes or [])
        self.delay = delay
        self.model_load_time_ms = 7.0
        self.calls = 0
        self.active = 0
        self.max_active = 0
        self.lock = threading.Lock()

    def transcribe(self, _request, _timeout_seconds):
        with self.lock:
            self.calls += 1
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            outcome = self.outcomes.pop(0) if self.outcomes else result()
        try:
            if self.delay:
                time.sleep(self.delay)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        finally:
            with self.lock:
                self.active -= 1


class FinalTranscriptionQueueTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.queues = []

    async def asyncTearDown(self):
        for queue in self.queues:
            await queue.close()

    def queue(self, transcriber, **overrides):
        values = {
            "timeout_seconds": 0.1,
            "max_retries": 0,
            "worker_concurrency": 1,
            "queue_capacity": 8,
        }
        values.update(overrides)
        queue = LocalFinalTranscriptionQueue(FinalTranscriptionConfig(**values), transcriber)
        self.queues.append(queue)
        return queue

    async def test_final_job_is_idempotent_and_duplicate_does_not_reprocess(self):
        transcriber = FakeTranscriber()
        queue = self.queue(transcriber)
        events = []

        async def listener(snapshot):
            events.append(snapshot)

        first, duplicate = await queue.enqueue(request(), listener)
        self.assertFalse(duplicate)
        await queue.join()
        event_count = len(events)
        second, duplicate = await queue.enqueue(request(), listener)
        self.assertTrue(duplicate)
        self.assertEqual(first.job_id, second.job_id)
        self.assertEqual(transcriber.calls, 1)
        self.assertEqual(len(events), event_count)

    async def test_retry_is_bounded_and_can_complete(self):
        transcriber = FakeTranscriber([RuntimeError("temporary"), result()])
        queue = self.queue(transcriber, max_retries=1)
        await queue.enqueue(request(), self._ignore)
        await queue.join()
        snapshot = queue.snapshot("session-a")[0]
        self.assertEqual(snapshot.status, FinalJobStatus.COMPLETED)
        self.assertEqual(snapshot.attempt, 2)
        self.assertEqual(queue.metrics()["retries"], 1)

    async def test_timeout_is_counted_and_fails_after_bound(self):
        transcriber = FakeTranscriber([FinalTranscriptionTimeout("deadline")])
        queue = self.queue(transcriber)
        await queue.enqueue(request(), self._ignore)
        await queue.join()
        self.assertEqual(queue.snapshot("session-a")[0].status, FinalJobStatus.FAILED)
        self.assertEqual(queue.metrics()["timeout_count"], 1)

    async def test_failure_status_preserves_existing_live_result(self):
        registry = LiveTranscriptStateRegistry()
        live = self._live_update(text="live text", revision=3)
        registry.apply(self._live_update(state=TranscriptState.PARTIAL, revision=1))
        registry.apply(self._live_update(state=TranscriptState.STABLE, revision=2))
        registry.apply(live)
        queue = self.queue(FakeTranscriber([RuntimeError("failed")]))
        await queue.enqueue(request(), self._ignore)
        await queue.join()
        self.assertEqual(registry.latest("session-a", "segment-1"), live)

    async def test_completed_final_replaces_live_result_once(self):
        registry = LiveTranscriptStateRegistry()
        registry.apply(self._live_update(state=TranscriptState.PARTIAL, revision=1))
        registry.apply(self._live_update(state=TranscriptState.STABLE, revision=2))
        registry.apply(self._live_update(revision=3))
        corrected = self._live_update(text="accurate", revision=4)
        self.assertTrue(registry.replace_with_accurate_final(corrected).accepted)
        self.assertEqual(registry.latest("session-a", "segment-1"), corrected)
        self.assertFalse(registry.replace_with_accurate_final(corrected).accepted)

    async def test_exact_model_metadata_is_retained(self):
        expected = result()
        queue = self.queue(FakeTranscriber([expected]))
        await queue.enqueue(request(), self._ignore)
        await queue.join()
        stored = queue.snapshot("session-a")[0].result
        self.assertEqual(stored, expected)
        self.assertEqual(len(stored.metadata.checkpoint_sha256), 64)
        self.assertEqual(stored.metadata.as_dict()["timestamps"][0]["startMs"], 200)

    async def test_sessions_are_isolated(self):
        queue = self.queue(FakeTranscriber())
        await queue.enqueue(request("session-a", "segment-a"), self._ignore)
        await queue.enqueue(request("session-b", "segment-b"), self._ignore)
        await queue.join()
        self.assertEqual([job.segment_id for job in queue.snapshot("session-a")], ["segment-a"])
        self.assertEqual([job.segment_id for job in queue.snapshot("session-b")], ["segment-b"])

    async def test_worker_concurrency_is_bounded(self):
        transcriber = FakeTranscriber(delay=0.04)
        queue = self.queue(transcriber, worker_concurrency=2)
        for index in range(4):
            await queue.enqueue(request(segment_id=f"segment-{index}"), self._ignore)
        await queue.join()
        self.assertEqual(transcriber.max_active, 2)
        self.assertLessEqual(transcriber.max_active, queue.config.worker_concurrency)

    async def test_metrics_cover_queue_lifecycle(self):
        queue = self.queue(FakeTranscriber())
        await queue.enqueue(request(), self._ignore)
        await queue.join()
        queue.record_replacement()
        metrics = queue.metrics()
        self.assertEqual(metrics["queued_final_jobs"], 1)
        self.assertEqual(metrics["completed"], 1)
        self.assertEqual(metrics["failed"], 0)
        self.assertEqual(metrics["queue_depth"], 0)
        self.assertEqual(metrics["model_load_time_ms"], 7)
        self.assertEqual(metrics["final_replacement_count"], 1)

    async def _ignore(self, _snapshot):
        return None

    @staticmethod
    def _live_update(
        *, text="live text", revision=3, state=TranscriptState.FINAL
    ):
        return LiveTranscriptUpdate(
            session_id="session-a",
            segment_id="segment-1",
            revision=revision,
            state=state,
            sequence_start=1,
            sequence_end=3,
            start_ms=200,
            end_ms=800,
            text=text,
            language="en",
            model="base",
            latency_ms=10,
        )


class PersistentFinalModelTests(unittest.TestCase):
    def test_model_is_loaded_once_and_retained(self):
        class FakeAdapter:
            effective_device = "cpu"

            def __init__(self):
                self.loads = 0

            def load_model(self, _model):
                self.loads += 1
                return object()

        adapter = FakeAdapter()
        transcriber = PersistentLocalFinalTranscriber(
            FinalTranscriptionConfig(),
            adapter=adapter,
            checkpoint_resolver=lambda _model: Path("C:/models/base.pt"),
        )
        transcriber.ensure_loaded()
        transcriber.ensure_loaded()
        self.assertEqual(adapter.loads, 1)
        self.assertGreaterEqual(transcriber.model_load_time_ms, 0)

    def test_local_transcriber_emits_exact_runtime_metadata(self):
        class FakeAdapter:
            effective_device = "cpu"

            def load_model(self, _model):
                return object()

            def transcribe(self, audio_path, **kwargs):
                self.audio_existed = audio_path.is_file()
                self.kwargs = kwargs
                return {
                    "text": "accurate text",
                    "language": "en",
                    "segments": [{"start": 0.1, "end": 0.5, "text": "accurate text"}],
                }

        adapter = FakeAdapter()
        checkpoint_path = Path("C:/models/base.pt")
        transcriber = PersistentLocalFinalTranscriber(
            FinalTranscriptionConfig(model="base", device="cpu", compute_type="float32"),
            adapter=adapter,
            checkpoint_resolver=lambda _model: checkpoint_path,
        )
        output = transcriber.transcribe(request(), 1)
        metadata = output.metadata
        self.assertTrue(adapter.audio_existed)
        self.assertEqual(metadata.model, "base")
        self.assertEqual(Path(metadata.checkpoint_path), checkpoint_path)
        self.assertEqual(metadata.device, "cpu")
        self.assertEqual(metadata.compute_type, "float32")
        self.assertEqual(metadata.language, "en")
        self.assertEqual(metadata.beam_size, 5)
        self.assertEqual(metadata.timestamps[0]["startMs"], 300)
        self.assertGreaterEqual(metadata.latency_ms, 0)

    def test_feature_flag_off_preserves_local_base_live_defaults(self):
        settings = Settings()
        self.assertFalse(settings.live_accurate_final_enabled)
        self.assertEqual(settings.live_final_model, "base")
        from app.models.live import CreateLiveSessionRequest

        self.assertEqual(CreateLiveSessionRequest().model, "base")


class FinalCorrectionIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_completed_worker_result_replaces_same_live_segment(self):
        live_route = import_module("app.routes.live")
        registry = LiveTranscriptStateRegistry()
        registry.apply(FinalTranscriptionQueueTests._live_update(state=TranscriptState.PARTIAL, revision=1))
        registry.apply(FinalTranscriptionQueueTests._live_update(state=TranscriptState.STABLE, revision=2))
        registry.apply(FinalTranscriptionQueueTests._live_update(revision=3))

        class QueueMetrics:
            replacements = 0

            def record_replacement(self):
                self.replacements += 1

            def metrics(self):
                return {"final_replacement_count": self.replacements}

        queue = QueueMetrics()
        completed = FinalJobSnapshot(
            job_id="job-1",
            session_id="session-a",
            segment_id="segment-1",
            status=FinalJobStatus.COMPLETED,
            attempt=1,
            result=result("accurate replacement"),
        )
        sender = AsyncMock()
        with (
            patch.object(live_route, "_final_queue", queue),
            patch.object(live_route, "_live_state_registry", registry),
            patch.object(live_route, "_send_to_current_connection", sender),
        ):
            await live_route._handle_final_job_status(completed)

        latest = registry.latest("session-a", "segment-1")
        self.assertEqual(latest.text, "accurate replacement")
        self.assertEqual(latest.revision, 4)
        self.assertEqual(queue.replacements, 1)
        payload = sender.await_args.args[1]
        self.assertEqual(payload["status"], "completed")
        self.assertEqual(payload["update"]["segmentId"], "segment-1")

    async def test_failed_worker_result_does_not_replace_live_segment(self):
        live_route = import_module("app.routes.live")
        registry = LiveTranscriptStateRegistry()
        registry.apply(FinalTranscriptionQueueTests._live_update(state=TranscriptState.PARTIAL, revision=1))
        registry.apply(FinalTranscriptionQueueTests._live_update(state=TranscriptState.STABLE, revision=2))
        live = FinalTranscriptionQueueTests._live_update(revision=3)
        registry.apply(live)

        class QueueMetrics:
            def metrics(self):
                return {"failed": 1}

        failed = FinalJobSnapshot(
            job_id="job-1",
            session_id="session-a",
            segment_id="segment-1",
            status=FinalJobStatus.FAILED,
            attempt=2,
            error="RuntimeError: failed",
        )
        with (
            patch.object(live_route, "_final_queue", QueueMetrics()),
            patch.object(live_route, "_live_state_registry", registry),
            patch.object(live_route, "_send_to_current_connection", AsyncMock()),
        ):
            await live_route._handle_final_job_status(failed)

        self.assertEqual(registry.latest("session-a", "segment-1"), live)


if __name__ == "__main__":
    unittest.main()

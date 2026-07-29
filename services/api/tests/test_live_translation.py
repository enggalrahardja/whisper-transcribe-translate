import asyncio
import threading
import time
import unittest
from datetime import datetime, timezone
from importlib import import_module
from unittest.mock import AsyncMock, patch

from app.config import Settings
from app.services.glossary import GlossarySnapshot, GlossaryTerm
from app.services.live_transcript_state import LiveTranscriptStateRegistry, LiveTranscriptUpdate, TranscriptState
from app.services.live_translation import (
    LiveTranslationConfig,
    LocalLiveTranslationQueue,
    PersistentLocalMarianTranslator,
    TranslationMetadata,
    TranslationRequest,
    TranslationResult,
    TranslationStatus,
    protect_glossary_terms,
    restore_glossary_terms,
)
from app.services.pcm_transcription import PcmTranscriptionResult
from app.models.live import LiveSessionResponse
from app.services.final_transcription import (
    FinalJobSnapshot,
    FinalJobStatus,
    FinalModelMetadata,
    FinalTranscriptionResult,
)


def request(
    *, session="session-a", segment="segment-1", revision=2,
    state=TranscriptState.STABLE, text="teks sumber", context_ids=(), context_texts=(), glossary=None,
):
    return TranslationRequest(
        session_id=session,
        segment_id=segment,
        source_revision=revision,
        source_state=state,
        source_text=text,
        source_language="id",
        target_language="en",
        context_segment_ids=context_ids,
        context_texts=context_texts,
        glossary=glossary,
    )


def translation_result(source_revision=2, text="translated", terms=()):
    now = datetime.now(timezone.utc)
    return TranslationResult(
        raw_text=text,
        text=text,
        glossary_terms_applied=terms,
        metadata=TranslationMetadata(
            provider="fake-local",
            model="local-test-model",
            checkpoint="abc123",
            locality="local",
            source_language="id",
            detected_language="id",
            target_language="en",
            context_segment_ids=(),
            glossary_version=None,
            device="cpu",
            compute_type="float32",
            latency_ms=3,
            source_revision=source_revision,
            detection_confidence=None,
            created_at=now,
            updated_at=now,
        ),
    )


class FakeTranslator:
    def __init__(self, outcomes=None, delay=0.0):
        self.outcomes = list(outcomes or [])
        self.delay = delay
        self.calls = 0
        self.active = 0
        self.max_active = 0
        self.model_load_time_ms = 5.0
        self.requests = []
        self.lock = threading.Lock()

    def translate(self, item, _timeout):
        with self.lock:
            self.calls += 1
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            self.requests.append(item)
            outcome = self.outcomes.pop(0) if self.outcomes else translation_result(item.source_revision)
        try:
            if self.delay:
                time.sleep(self.delay)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        finally:
            with self.lock:
                self.active -= 1


class LiveTranslationQueueTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.queues = []

    async def asyncTearDown(self):
        for queue in self.queues:
            await queue.close()

    def queue(self, translator=None, **overrides):
        values = dict(timeout_seconds=1, max_retries=0, worker_concurrency=1, queue_capacity=8)
        values.update(overrides)
        queue = LocalLiveTranslationQueue(LiveTranslationConfig(**values), translator or FakeTranslator())
        self.queues.append(queue)
        return queue

    async def test_stable_produces_preview_and_final_produces_completed(self):
        queue = self.queue()
        await queue.enqueue(request(), self.ignore)
        await queue.join()
        self.assertEqual(queue.snapshot("session-a")[0].status, TranslationStatus.PREVIEW)
        await queue.enqueue(request(revision=3, state=TranscriptState.FINAL), self.ignore)
        await queue.join()
        final = queue.snapshot("session-a")[0]
        self.assertEqual(final.status, TranslationStatus.COMPLETED)
        self.assertEqual(final.source_revision, 3)
        self.assertEqual(queue.metrics()["replacement_count"], 1)

    async def test_duplicate_is_idempotent_and_not_reprocessed(self):
        translator = FakeTranslator()
        queue = self.queue(translator)
        first = await queue.enqueue(request(), self.ignore)
        await queue.join()
        duplicate = await queue.enqueue(request(), self.ignore)
        self.assertTrue(first.accepted)
        self.assertFalse(duplicate.accepted)
        self.assertEqual(duplicate.reason, "duplicate")
        self.assertEqual(translator.calls, 1)

    async def test_out_of_order_revision_is_rejected(self):
        queue = self.queue()
        await queue.enqueue(request(revision=3, state=TranscriptState.FINAL), self.ignore)
        outcome = await queue.enqueue(request(revision=2), self.ignore)
        self.assertFalse(outcome.accepted)
        self.assertEqual(outcome.reason, "out_of_order")
        self.assertEqual(queue.metrics()["rejected_out_of_order"], 1)

    async def test_context_and_language_pair_reach_local_translator(self):
        translator = FakeTranslator()
        queue = self.queue(translator)
        item = request(context_ids=("segment-0",), context_texts=("konteks sebelumnya",))
        await queue.enqueue(item, self.ignore)
        await queue.join()
        received = translator.requests[0]
        self.assertEqual(received.context_segment_ids, ("segment-0",))
        self.assertEqual(received.context_texts, ("konteks sebelumnya",))
        self.assertEqual((received.source_language, received.target_language), ("id", "en"))

    async def test_failure_does_not_mutate_source_transcript(self):
        registry = LiveTranscriptStateRegistry()
        source = live_update()
        registry.apply(live_update(revision=1, state=TranscriptState.PARTIAL))
        registry.apply(live_update(revision=2, state=TranscriptState.STABLE))
        registry.apply(source)
        queue = self.queue(FakeTranslator([RuntimeError("translation failed")]))
        await queue.enqueue(request(revision=3, state=TranscriptState.FINAL, text=source.text), self.ignore)
        await queue.join()
        self.assertEqual(queue.snapshot("session-a")[0].status, TranslationStatus.FAILED)
        self.assertEqual(registry.latest("session-a", "segment-1"), source)

    async def test_retry_and_concurrency_are_bounded(self):
        retry_translator = FakeTranslator([RuntimeError("temporary"), translation_result()])
        retry_queue = self.queue(retry_translator, max_retries=1)
        await retry_queue.enqueue(request(), self.ignore)
        await retry_queue.join()
        self.assertEqual(retry_queue.metrics()["retries"], 1)

        concurrent = FakeTranslator(delay=0.03)
        queue = self.queue(concurrent, worker_concurrency=2)
        for index in range(5):
            await queue.enqueue(request(segment=f"segment-{index}"), self.ignore)
        await queue.join()
        self.assertLessEqual(concurrent.max_active, 2)

    async def test_queue_backpressure_is_bounded(self):
        queue = self.queue(FakeTranslator(delay=0.02), queue_capacity=1)
        await queue.enqueue(request(segment="segment-a"), self.ignore)
        with self.assertRaises(asyncio.QueueFull):
            await queue.enqueue(request(segment="segment-b"), self.ignore)
        await queue.join()

    async def test_sessions_are_isolated_and_snapshot_is_latest_only(self):
        queue = self.queue()
        await queue.enqueue(request(session="session-a", segment="a"), self.ignore)
        await queue.enqueue(request(session="session-b", segment="b"), self.ignore)
        await queue.join()
        self.assertEqual([item.segment_id for item in queue.snapshot("session-a")], ["a"])
        self.assertEqual([item.segment_id for item in queue.snapshot("session-b")], ["b"])

    async def test_reconnect_snapshot_restores_latest_completed_revision(self):
        queue = self.queue()
        await queue.enqueue(request(), self.ignore)
        await queue.join()
        await queue.enqueue(request(revision=3, state=TranscriptState.FINAL), self.ignore)
        await queue.join()
        restored = queue.snapshot("session-a")
        self.assertEqual(len(restored), 1)
        self.assertEqual(restored[0].status, TranslationStatus.COMPLETED)
        self.assertEqual(restored[0].translation_revision, 2)

    async def test_metadata_contract_is_machine_readable(self):
        queue = self.queue()
        await queue.enqueue(request(), self.ignore)
        await queue.join()
        payload = queue.snapshot("session-a")[0].as_dict()
        self.assertEqual(payload["sourceText"], "teks sumber")
        self.assertEqual(payload["status"], "preview")
        metadata = payload["metadata"]
        for field in (
            "provider", "model", "checkpoint", "localCloud", "sourceLanguage",
            "detectedLanguage", "targetLanguage", "contextSegmentIds",
            "glossaryVersion", "device", "computeType", "latencyMs", "revision",
            "createdAt", "updatedAt",
        ):
            self.assertIn(field, metadata)

    async def test_metrics_cover_required_lifecycle(self):
        result = translation_result(2, terms=("SMARTHub",))
        queue = self.queue(FakeTranslator([result]))
        await queue.enqueue(request(), self.ignore)
        await queue.join()
        metrics = queue.metrics()
        self.assertEqual(metrics["queued_translation_jobs"], 1)
        self.assertEqual(metrics["completed"], 1)
        self.assertEqual(metrics["failed"], 0)
        self.assertEqual(metrics["queue_depth"], 0)
        self.assertEqual(metrics["glossary_terms_applied"], 1)
        self.assertEqual(metrics["model_load_time_ms"], 5)

    async def ignore(self, _snapshot):
        return None


class GlossaryTranslationTests(unittest.TestCase):
    def snapshot(self):
        terms = (
            GlossaryTerm(
                preferred_spelling="SMARTHub", aliases=("Smart Hub",), do_not_change=False,
                category="product", priority=100, language="*", active=True,
                preferred_translations=(("en", "SMARTHub"),), do_not_translate=True,
            ),
            GlossaryTerm(
                preferred_spelling="pusat data", aliases=(), do_not_change=False,
                category="technical", priority=90, language="id", active=True,
                preferred_translations=(("en", "data center"),),
            ),
        )
        return GlossarySnapshot("v1", terms, "", lambda *_: None)

    def test_do_not_translate_and_preferred_translation_are_marker_protected(self):
        protected, replacements = protect_glossary_terms(
            "Smart Hub terhubung ke pusat data.", self.snapshot(), "en"
        )
        self.assertNotIn("Smart Hub", protected)
        self.assertNotIn("pusat data", protected)
        raw = protected.replace("terhubung ke", "connects to")
        corrected, applied = restore_glossary_terms(raw, replacements)
        self.assertIn("SMARTHub", corrected)
        self.assertIn("data center", corrected)
        self.assertEqual(set(applied), {"SMARTHub", "data center"})


class PersistentModelAndFeatureTests(unittest.TestCase):
    def test_model_loader_is_persistent(self):
        calls = 0

        def loader():
            nonlocal calls
            calls += 1
            return object(), object(), "cpu", "float32", "checkpoint-sha"

        translator = PersistentLocalMarianTranslator(LiveTranslationConfig(), model_loader=loader)
        translator.ensure_loaded()
        translator.ensure_loaded()
        self.assertEqual(calls, 1)

    def test_feature_flag_defaults_off_and_live_model_stays_base(self):
        settings = Settings()
        self.assertFalse(settings.live_translation_enabled)
        from app.models.live import CreateLiveSessionRequest
        self.assertEqual(CreateLiveSessionRequest().model, "base")


class LiveTranslationRouteIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_feature_flag_off_does_not_enqueue_translation(self):
        route = import_module("app.routes.live")
        now = datetime.now(timezone.utc)
        session = LiveSessionResponse(
            session_id="session-a", status="active", language="id", model="base",
            started_at=now, duration=1, partial_text="sumber", final_text="",
            segments=[], created_at=now, updated_at=now,
        )
        detail = PcmTranscriptionResult(
            session=session, duplicate=False, segment_id="segment-1",
            sequence_start=1, sequence_end=2, text="sumber", raw_text="sumber",
            glossary_corrections=(), glossary_version=None,
            start_ms=0, end_ms=1000, latency_ms=10,
        )
        enqueue = AsyncMock()
        with (
            patch.object(route, "_live_state_registry", LiveTranscriptStateRegistry()),
            patch.object(route, "_send_to_current_connection", AsyncMock()),
            patch.object(route, "_enqueue_live_translation", enqueue),
            patch.object(route._runtime_settings, "live_translation_enabled", False),
        ):
            await route._send_live_transcript_lifecycle("session-a", detail)
        enqueue.assert_not_awaited()

    async def test_semantic_lifecycle_enqueues_only_stable_and_final(self):
        route = import_module("app.routes.live")
        now = datetime.now(timezone.utc)
        session = LiveSessionResponse(
            session_id="session-a", status="active", language="id", model="base",
            started_at=now, duration=1, partial_text="sumber", final_text="",
            segments=[], created_at=now, updated_at=now,
        )
        detail = PcmTranscriptionResult(
            session=session, duplicate=False, segment_id="segment-1",
            sequence_start=1, sequence_end=2, text="sumber", raw_text="sumber",
            glossary_corrections=(), glossary_version=None,
            start_ms=0, end_ms=1000, latency_ms=10,
        )
        registry = LiveTranscriptStateRegistry()
        enqueue = AsyncMock()
        with (
            patch.object(route, "_live_state_registry", registry),
            patch.object(route, "_send_to_current_connection", AsyncMock()),
            patch.object(route, "_enqueue_live_translation", enqueue),
            patch.object(route._runtime_settings, "live_translation_enabled", True),
        ):
            await route._send_live_transcript_lifecycle("session-a", detail)
        self.assertEqual(enqueue.await_count, 2)
        self.assertEqual(
            [call.args[0].state for call in enqueue.await_args_list],
            [TranscriptState.STABLE, TranscriptState.FINAL],
        )

    async def test_accurate_final_is_enqueued_as_new_priority_source(self):
        route = import_module("app.routes.live")
        registry = LiveTranscriptStateRegistry()
        registry.apply(live_update(revision=1, state=TranscriptState.PARTIAL))
        registry.apply(live_update(revision=2, state=TranscriptState.STABLE))
        registry.apply(live_update())

        class QueueMetrics:
            def record_replacement(self):
                return None

            def metrics(self):
                return {}

        now = datetime.now(timezone.utc)
        final = FinalTranscriptionResult(
            text="accurate source", raw_text="accurate source",
            metadata=FinalModelMetadata(
                model="base", checkpoint_path="base.pt", checkpoint_sha256="a" * 64,
                device="cpu", compute_type="float32", language="id", beam_size=5,
                timestamps=(), latency_ms=20,
            ),
        )
        snapshot = FinalJobSnapshot(
            job_id="final-job", session_id="session-a", segment_id="segment-1",
            status=FinalJobStatus.COMPLETED, attempt=1, result=final,
            queued_at=now, started_at=now, completed_at=now,
        )
        enqueue = AsyncMock()
        with (
            patch.object(route, "_live_state_registry", registry),
            patch.object(route, "_final_queue", QueueMetrics()),
            patch.object(route, "_send_to_current_connection", AsyncMock()),
            patch.object(route, "_enqueue_live_translation", enqueue),
            patch.object(route._runtime_settings, "live_translation_enabled", True),
        ):
            await route._handle_final_job_status(snapshot)
        replacement = enqueue.await_args.args[0]
        self.assertEqual(replacement.revision, 4)
        self.assertEqual(replacement.state, TranscriptState.FINAL)
        self.assertEqual(replacement.text, "accurate source")


def live_update(revision=3, state=TranscriptState.FINAL):
    return LiveTranscriptUpdate(
        session_id="session-a", segment_id="segment-1", revision=revision, state=state,
        sequence_start=0, sequence_end=1, start_ms=0, end_ms=200,
        text="source transcript", language="id", model="base", latency_ms=4,
    )


if __name__ == "__main__":
    unittest.main()

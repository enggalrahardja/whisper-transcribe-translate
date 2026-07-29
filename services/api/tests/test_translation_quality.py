import asyncio
import threading
import time
import unittest
from datetime import datetime, timezone
from importlib import import_module
from unittest.mock import AsyncMock, patch

from app.config import Settings
from app.services.glossary import GlossarySnapshot, GlossaryTerm
from app.services.live_transcript_state import TranscriptState
from app.services.live_translation import (
    TranslationMetadata,
    TranslationResult,
    TranslationSnapshot,
    TranslationStatus,
)
from app.services.translation_quality import (
    DeterministicTranslationQualityProcessor,
    LocalTranslationQualityQueue,
    QualityStatus,
    TranslationQualityConfig,
    TranslationQualityRequest,
    TranslationQualityResult,
)


def glossary_snapshot():
    terms = (
        GlossaryTerm(
            preferred_spelling="SMARTHub", aliases=("Smart Hub",),
            do_not_change=False, category="product", priority=100,
            language="*", active=True,
            preferred_translations=(("en", "SMARTHub"),), do_not_translate=True,
        ),
        GlossaryTerm(
            preferred_spelling="pusat data", aliases=(),
            do_not_change=False, category="technical", priority=90,
            language="id", active=True,
            preferred_translations=(("en", "data center"),), do_not_translate=False,
        ),
    )
    return GlossarySnapshot("glossary-v1", terms, "", lambda *_: None)


def quality_request(
    text="  hello   world  ", *, session="session-a", segment="segment-1",
    revision=1, source="halo dunia", glossary=None,
):
    return TranslationQualityRequest(
        session_id=session,
        segment_id=segment,
        translation_revision=revision,
        source_text=source,
        raw_model_translation=text,
        final_translation=text,
        source_language="id",
        target_language="en",
        glossary_version=getattr(glossary, "version", None),
        start_ms=100,
        end_ms=900,
        glossary=glossary,
    )


class TranslationQualityRuleTests(unittest.TestCase):
    def setUp(self):
        self.processor = DeterministicTranslationQualityProcessor()

    def correct(self, text, **kwargs):
        return self.processor.process(quality_request(text, **kwargs))

    def test_whitespace_normalization(self):
        result = self.correct("  Hello   world.  ")
        self.assertEqual(result.corrected_translation, "Hello world.")
        self.assertIn("whitespace", [item.rule for item in result.applied_corrections])

    def test_punctuation_normalization(self):
        result = self.correct("Hello world  !!")
        self.assertEqual(result.corrected_translation, "Hello world!")
        self.assertIn("punctuation", [item.rule for item in result.applied_corrections])

    def test_capitalization(self):
        result = self.correct("hello world. next sentence.")
        self.assertEqual(result.corrected_translation, "Hello world. Next sentence.")
        self.assertIn("capitalization", [item.rule for item in result.applied_corrections])

    def test_duplicate_sentence_and_phrase_are_removed(self):
        sentence = self.correct("We are ready. We are ready.")
        phrase = self.correct("We are ready we are ready.")
        self.assertEqual(sentence.corrected_translation, "We are ready.")
        self.assertEqual(phrase.corrected_translation, "We are ready.")

    def test_numbers_codes_and_versions_are_preserved(self):
        raw = "release FX9600 v2.4 with 1,250 units"
        result = self.correct(raw)
        for value in ("FX9600", "v2.4", "1,250"):
            self.assertIn(value, result.corrected_translation)
        self.assertGreaterEqual(result.protected_value_count, 3)

    def test_dates_and_times_are_preserved(self):
        raw = "meeting on 2026-07-29 at 09:30"
        result = self.correct(raw)
        self.assertIn("2026-07-29", result.corrected_translation)
        self.assertIn("09:30", result.corrected_translation)
        self.assertGreaterEqual(result.protected_value_count, 2)

    def test_do_not_translate_enforcement(self):
        glossary = glossary_snapshot()
        result = self.correct(
            "Smart Hub is ready.",
            source="Smart Hub sudah siap.",
            glossary=glossary,
        )
        self.assertEqual(result.corrected_translation, "SMARTHub is ready.")
        self.assertEqual(result.terminology_corrections, 1)

    def test_product_name_case_is_not_changed(self):
        product = GlossaryTerm(
            preferred_spelling="iPhone", aliases=(), do_not_change=True,
            category="product", priority=100, language="*", active=True,
            preferred_translations=(), do_not_translate=True,
        )
        glossary = GlossarySnapshot("product-v1", (product,), "", lambda *_: None)
        result = self.correct(
            "iPhone is ready",
            source="iPhone sudah siap",
            glossary=glossary,
        )
        self.assertEqual(result.corrected_translation, "iPhone is ready.")

    def test_preferred_translation_enforcement(self):
        glossary = glossary_snapshot()
        result = self.correct(
            "The pusat data is ready.",
            source="Pusat data sudah siap.",
            glossary=glossary,
        )
        self.assertEqual(result.corrected_translation, "The data center is ready.")
        self.assertEqual(result.terminology_corrections, 1)

    def test_correction_is_idempotent(self):
        first = self.correct("  hello world!! hello world!!  ")
        second = self.correct(first.corrected_translation)
        self.assertEqual(second.corrected_translation, first.corrected_translation)
        self.assertEqual(second.applied_corrections, ())

    def test_negation_speaker_and_timestamp_metadata_are_unchanged(self):
        raw = "Speaker 1: do not remove version v1.2 at 10:15"
        request = quality_request(raw)
        result = self.processor.process(request)
        self.assertIn("Speaker 1:", result.corrected_translation)
        self.assertIn("not", result.corrected_translation)
        self.assertEqual((request.start_ms, request.end_ms), (100, 900))


class FakeProcessor:
    def __init__(self, outcomes=None, delay=0):
        self.outcomes = list(outcomes or [])
        self.delay = delay
        self.calls = 0
        self.active = 0
        self.max_active = 0
        self.lock = threading.Lock()

    def process(self, request):
        with self.lock:
            self.calls += 1
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            outcome = self.outcomes.pop(0) if self.outcomes else TranslationQualityResult(
                raw_translation=request.final_translation,
                corrected_translation=request.final_translation.strip().capitalize() + ".",
                applied_corrections=(), latency_ms=2,
                terminology_corrections=0, protected_value_count=0,
            )
        try:
            if self.delay:
                time.sleep(self.delay)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        finally:
            with self.lock:
                self.active -= 1


class TranslationQualityQueueTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.queues = []

    async def asyncTearDown(self):
        for queue in self.queues:
            await queue.close()

    def queue(self, processor=None, **overrides):
        values = dict(timeout_seconds=1, max_retries=0, worker_concurrency=1, queue_capacity=8)
        values.update(overrides)
        queue = LocalTranslationQualityQueue(
            TranslationQualityConfig(**values), processor or FakeProcessor()
        )
        self.queues.append(queue)
        return queue

    async def test_completed_job_retains_raw_and_corrected_translation(self):
        queue = self.queue()
        await queue.enqueue(quality_request("hello"), self.ignore)
        await queue.join()
        result = queue.snapshot("session-a")[0]
        self.assertEqual(result.status, QualityStatus.COMPLETED)
        self.assertEqual(result.raw_translation, "hello")
        self.assertEqual(result.corrected_translation, "Hello.")
        self.assertEqual((result.start_ms, result.end_ms), (100, 900))
        self.assertEqual((result.source_language, result.target_language), ("id", "en"))

    async def test_failure_falls_back_to_raw_final_translation(self):
        queue = self.queue(FakeProcessor([RuntimeError("quality failed")]))
        await queue.enqueue(quality_request("raw final"), self.ignore)
        await queue.join()
        result = queue.snapshot("session-a")[0]
        self.assertEqual(result.status, QualityStatus.FAILED)
        self.assertTrue(result.fallback)
        self.assertEqual(result.corrected_translation, "raw final")
        self.assertEqual(queue.metrics()["fallback_count"], 1)
        self.assertEqual(queue.metrics()["processed_quality_jobs"], 1)

    async def test_duplicate_job_is_idempotent(self):
        processor = FakeProcessor()
        queue = self.queue(processor)
        await queue.enqueue(quality_request(), self.ignore)
        await queue.join()
        duplicate = await queue.enqueue(quality_request(), self.ignore)
        self.assertFalse(duplicate.accepted)
        self.assertEqual(duplicate.reason, "duplicate")
        self.assertEqual(processor.calls, 1)

    async def test_sessions_are_isolated(self):
        queue = self.queue()
        await queue.enqueue(quality_request(session="session-a", segment="a"), self.ignore)
        await queue.enqueue(quality_request(session="session-b", segment="b"), self.ignore)
        await queue.join()
        self.assertEqual([item.segment_id for item in queue.snapshot("session-a")], ["a"])
        self.assertEqual([item.segment_id for item in queue.snapshot("session-b")], ["b"])

    async def test_retry_queue_and_concurrency_are_bounded(self):
        retry = FakeProcessor([RuntimeError("temporary"), TranslationQualityResult(
            raw_translation="raw", corrected_translation="Raw.", applied_corrections=(),
            latency_ms=1, terminology_corrections=0, protected_value_count=0,
        )])
        retry_queue = self.queue(retry, max_retries=1)
        await retry_queue.enqueue(quality_request("raw"), self.ignore)
        await retry_queue.join()
        self.assertEqual(retry_queue.metrics()["retries"], 1)

        processor = FakeProcessor(delay=0.03)
        queue = self.queue(processor, worker_concurrency=2)
        for index in range(4):
            await queue.enqueue(quality_request(segment=f"segment-{index}"), self.ignore)
        await queue.join()
        self.assertLessEqual(processor.max_active, 2)

    async def test_queue_capacity_applies_backpressure(self):
        queue = self.queue(FakeProcessor(delay=0.02), queue_capacity=1)
        await queue.enqueue(quality_request(segment="segment-a"), self.ignore)
        with self.assertRaises(asyncio.QueueFull):
            await queue.enqueue(quality_request(segment="segment-b"), self.ignore)
        await queue.join()

    async def test_timeout_is_bounded_and_falls_back(self):
        queue = self.queue(
            FakeProcessor(delay=0.05),
            timeout_seconds=0.005,
            max_retries=0,
        )
        await queue.enqueue(quality_request("raw timeout"), self.ignore)
        await queue.join()
        result = queue.snapshot("session-a")[0]
        self.assertEqual(result.status, QualityStatus.FAILED)
        self.assertTrue(result.fallback)
        self.assertEqual(result.corrected_translation, "raw timeout")

    async def test_metrics_cover_quality_contract(self):
        queue = LocalTranslationQualityQueue(
            TranslationQualityConfig(max_retries=0),
            DeterministicTranslationQualityProcessor(),
        )
        self.queues.append(queue)
        await queue.enqueue(quality_request("release v2.0 on 2026-07-29"), self.ignore)
        await queue.join()
        metrics = queue.metrics()
        self.assertEqual(metrics["processed_quality_jobs"], 1)
        self.assertGreater(metrics["corrections_applied"], 0)
        self.assertGreaterEqual(metrics["number_date_protection_count"], 2)
        self.assertEqual(metrics["failed_jobs"], 0)

    async def ignore(self, _snapshot):
        return None


def completed_translation_snapshot(status=TranslationStatus.COMPLETED):
    now = datetime.now(timezone.utc)
    metadata = TranslationMetadata(
        provider="fake-local", model="local-model", checkpoint="abc",
        locality="local", source_language="id", detected_language="id",
        target_language="en", context_segment_ids=(), glossary_version=None,
        device="cpu", compute_type="float32", latency_ms=5,
        source_revision=3, detection_confidence=None,
        created_at=now, updated_at=now, start_ms=100, end_ms=900,
    )
    result = TranslationResult("raw final", "final translation", (), metadata)
    return TranslationSnapshot(
        job_id="translation-job", session_id="session-a", segment_id="segment-1",
        source_revision=3, source_state=TranscriptState.FINAL,
        source_text="sumber", status=status, translation_revision=1,
        result=result if status in {TranslationStatus.COMPLETED, TranslationStatus.PREVIEW} else None,
    )


class TranslationQualityIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_completed_translation_enters_quality_queue(self):
        route = import_module("app.routes.live")

        class TranslationQueue:
            def metrics(self):
                return {}

        enqueue = AsyncMock()
        with (
            patch.object(route, "_translation_queue", TranslationQueue()),
            patch.object(route, "_send_to_current_connection", AsyncMock()),
            patch.object(route, "_enqueue_translation_quality", enqueue),
            patch.object(route._runtime_settings, "live_translation_quality_enabled", True),
        ):
            await route._handle_translation_status(completed_translation_snapshot(TranslationStatus.PREVIEW))
            enqueue.assert_not_awaited()
            await route._handle_translation_status(completed_translation_snapshot())
            enqueue.assert_awaited_once()

    async def test_feature_flag_off_does_not_enqueue_quality(self):
        route = import_module("app.routes.live")

        class TranslationQueue:
            def metrics(self):
                return {}

        enqueue = AsyncMock()
        with (
            patch.object(route, "_translation_queue", TranslationQueue()),
            patch.object(route, "_send_to_current_connection", AsyncMock()),
            patch.object(route, "_enqueue_translation_quality", enqueue),
            patch.object(route._runtime_settings, "live_translation_quality_enabled", False),
        ):
            await route._handle_translation_status(completed_translation_snapshot())
        enqueue.assert_not_awaited()
        self.assertFalse(Settings().live_translation_quality_enabled)


if __name__ == "__main__":
    unittest.main()

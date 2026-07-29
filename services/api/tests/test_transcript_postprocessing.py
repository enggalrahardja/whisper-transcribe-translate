import asyncio
import time
import unittest

from app.config import Settings
from app.services.transcript_postprocessing import (
    DeterministicTranscriptProcessor,
    LocalTranscriptPostprocessQueue,
    TranscriptPostprocessConfig,
    TranscriptPostprocessRequest,
    TranscriptPostprocessResult,
    TranscriptPostprocessStatus,
)


def request(text="hello world", *, session="session-a", segment="segment-1", revision=3, kind="final"):
    return TranscriptPostprocessRequest(
        session_id=session, segment_id=segment, source_revision=revision,
        source_kind=kind, raw_transcript=text,
        glossary_corrected_transcript=text, language="en", model="base",
        sequence_start=1, sequence_end=4, start_ms=100, end_ms=900,
    )


class ProcessorTests(unittest.TestCase):
    def process(self, text, **config):
        processor = DeterministicTranscriptProcessor(TranscriptPostprocessConfig(**config))
        return processor.process(request(text)).text

    def test_punctuation_capitalization_and_whitespace(self):
        self.assertEqual(self.process("  hello  ,world!!  "), "Hello, world!")

    def test_duplicate_phrase(self):
        self.assertEqual(self.process("ready now ready now"), "Ready now.")

    def test_filler_words_configurable(self):
        self.assertIn("Um", self.process("um ready", filler_mode="preserve"))
        self.assertEqual(self.process("um, ready", filler_mode="remove"), "Ready.")

    def test_numbers_dates_and_time_formatting(self):
        value = self.process("total 1 000 on 2026/07/29 at 09.30")
        self.assertEqual(value, "Total 1,000 on 2026-07-29 at 09:30.")

    def test_url_email_product_code_and_version_preserved(self):
        value = "visit https://example.com/a?x=1 email Dev@Test.IO for FX9600 v2.1 RFID"
        result = self.process(value)
        for token in ("https://example.com/a?x=1", "Dev@Test.IO", "FX9600", "v2.1", "RFID"):
            self.assertIn(token, result)

    def test_negation_and_speaker_attribution_preserved(self):
        value = "Speaker 1: do not remove this"
        result = self.process(value)
        self.assertIn("Speaker 1:", result)
        self.assertIn("not", result)

    def test_paragraph_segmentation(self):
        result = self.process("one. two. three. four.", paragraph_sentences=2)
        self.assertEqual(result, "One. Two.\n\nThree. Four.")

    def test_idempotent(self):
        first = self.process("  hello hello  world world. next sentence.", paragraph_sentences=1)
        self.assertEqual(self.process(first, paragraph_sentences=1), first)

    def test_partial_input_is_rejected(self):
        with self.assertRaises(ValueError):
            DeterministicTranscriptProcessor(TranscriptPostprocessConfig()).process(
                request("partial", kind="partial")
            )


class FailingProcessor:
    def process(self, _request):
        raise RuntimeError("failure")


class SlowProcessor:
    def process(self, source):
        time.sleep(0.03)
        return TranscriptPostprocessResult(source.glossary_corrected_transcript, (), 30, 0, 0, 0)


class QueueTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.queues = []

    async def asyncTearDown(self):
        for queue in self.queues:
            await queue.close()

    def queue(self, processor=None, **overrides):
        values = dict(timeout_seconds=1, max_retries=0, worker_concurrency=1, queue_capacity=8)
        values.update(overrides)
        queue = LocalTranscriptPostprocessQueue(TranscriptPostprocessConfig(**values), processor)
        self.queues.append(queue)
        return queue

    async def ignore(self, _snapshot):
        pass

    async def test_duplicate_job_idempotent(self):
        queue = self.queue()
        first = await queue.enqueue(request(), self.ignore)
        await queue.join()
        duplicate = await queue.enqueue(request(), self.ignore)
        self.assertTrue(first.accepted)
        self.assertFalse(duplicate.accepted)
        self.assertEqual(queue.metrics()["post_processing_jobs"], 1)

    async def test_failure_falls_back_to_glossary_corrected_transcript(self):
        queue = self.queue(FailingProcessor())
        await queue.enqueue(request("keep this"), self.ignore)
        await queue.join()
        snapshot = queue.snapshot("session-a")[0]
        self.assertEqual(snapshot.status, TranscriptPostprocessStatus.FAILED)
        self.assertEqual(snapshot.post_processed_transcript, "keep this")
        self.assertTrue(snapshot.fallback)

    async def test_sessions_are_isolated(self):
        queue = self.queue()
        await queue.enqueue(request(session="a"), self.ignore)
        await queue.enqueue(request(session="b"), self.ignore)
        await queue.join()
        self.assertEqual(len(queue.snapshot("a")), 1)
        self.assertEqual(len(queue.snapshot("b")), 1)

    async def test_accurate_final_supersedes_live_final(self):
        queue = self.queue()
        await queue.enqueue(request("live", revision=3), self.ignore)
        await queue.join()
        await queue.enqueue(request("accurate", revision=4, kind="accurate_final"), self.ignore)
        await queue.join()
        latest = queue.snapshot("session-a")[0]
        self.assertEqual(latest.source_kind, "accurate_final")
        self.assertEqual(latest.raw_transcript, "accurate")

    async def test_out_of_order_final_rejected(self):
        queue = self.queue()
        await queue.enqueue(request(revision=4, kind="accurate_final"), self.ignore)
        await queue.join()
        outcome = await queue.enqueue(request(revision=3), self.ignore)
        self.assertFalse(outcome.accepted)
        self.assertEqual(outcome.reason, "out_of_order")

    async def test_timeout_retry_capacity_and_metrics_bounded(self):
        queue = self.queue(SlowProcessor(), timeout_seconds=0.005, max_retries=1, queue_capacity=1)
        await queue.enqueue(request(segment="a"), self.ignore)
        with self.assertRaises(asyncio.QueueFull):
            await queue.enqueue(request(segment="b"), self.ignore)
        await queue.join()
        metrics = queue.metrics()
        self.assertEqual(metrics["retries"], 1)
        self.assertEqual(metrics["failed"], 1)
        self.assertEqual(metrics["fallback_count"], 1)
        self.assertEqual(metrics["queue_depth"], 0)

    async def test_raw_and_glossary_outputs_remain_separate(self):
        source = request("corrected")
        source = type(source)(**{**source.__dict__, "raw_transcript": "raw"})
        queue = self.queue()
        await queue.enqueue(source, self.ignore)
        await queue.join()
        result = queue.snapshot("session-a")[0]
        self.assertEqual(result.raw_transcript, "raw")
        self.assertEqual(result.glossary_corrected_transcript, "corrected")

    def test_feature_flag_defaults_off_and_legacy_model_unchanged(self):
        settings = Settings()
        self.assertFalse(settings.live_transcript_postprocess_enabled)
        from app.models.live import CreateLiveSessionRequest
        self.assertEqual(CreateLiveSessionRequest().model, "base")


if __name__ == "__main__":
    unittest.main()

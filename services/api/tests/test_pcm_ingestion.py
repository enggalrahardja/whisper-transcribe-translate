import unittest

from app.services.pcm_ingestion import (
    PcmChunkMetadata,
    PcmIngestionRegistry,
    PcmProtocolError,
)


def metadata(session_id: str, sequence: int, duration_ms: float = 200.0) -> PcmChunkMetadata:
    return PcmChunkMetadata(
        session_id=session_id,
        sequence=sequence,
        capture_timestamp_ms=1_000.0 + sequence * duration_ms,
        sample_rate=16_000,
        channel_count=1,
        chunk_duration_ms=duration_ms,
        byte_length=round(16_000 * 2 * duration_ms / 1000),
    )


def audio(sequence: int, duration_ms: float = 200.0) -> bytes:
    byte_length = round(16_000 * 2 * duration_ms / 1000)
    return bytes([sequence % 251]) * byte_length


class PcmIngestionTests(unittest.TestCase):
    def test_contiguous_sequence_is_acknowledged_and_buffered(self):
        registry = PcmIngestionRegistry(max_buffer_seconds=2)
        outcome = registry.ingest("alpha", metadata("alpha", 0), audio(0))
        self.assertEqual(outcome.status, "accepted")
        self.assertEqual(outcome.expected_sequence, 1)
        self.assertEqual(outcome.metrics["chunks_sent"], 1)
        self.assertEqual(outcome.metrics["chunks_acknowledged"], 1)
        self.assertEqual(outcome.metrics["buffer_depth_ms"], 200.0)
        acknowledgement = outcome.acknowledgement()
        self.assertEqual(acknowledgement["sequence"], 0)
        self.assertEqual(acknowledgement["expectedSequence"], 1)
        self.assertEqual(acknowledgement["status"], "accepted")

    def test_duplicate_does_not_duplicate_audio(self):
        registry = PcmIngestionRegistry(max_buffer_seconds=2)
        registry.ingest("alpha", metadata("alpha", 0), audio(0))
        duplicate = registry.ingest("alpha", metadata("alpha", 0), audio(0))
        self.assertEqual(duplicate.status, "duplicate")
        self.assertEqual(duplicate.metrics["duplicate_chunks"], 1)
        window = registry.take_audio("alpha", target_duration_ms=200)
        self.assertIsNotNone(window)
        self.assertEqual(window.audio, audio(0))
        self.assertIsNone(registry.take_audio("alpha", target_duration_ms=1, flush=True))

    def test_gap_is_reported_and_late_chunk_restores_order(self):
        registry = PcmIngestionRegistry(max_buffer_seconds=2)
        registry.ingest("alpha", metadata("alpha", 0), audio(0))
        gap = registry.ingest("alpha", metadata("alpha", 2), audio(2))
        self.assertEqual(gap.status, "out_of_order")
        self.assertEqual(gap.expected_sequence, 1)
        self.assertEqual(gap.missing_sequences, (1,))
        self.assertEqual(gap.metrics["chunks_lost"], 1)

        recovered = registry.ingest("alpha", metadata("alpha", 1), audio(1))
        self.assertEqual(recovered.expected_sequence, 3)
        self.assertEqual(recovered.missing_sequences, ())
        self.assertEqual(recovered.metrics["chunks_lost"], 1)
        window = registry.take_audio("alpha", target_duration_ms=600)
        self.assertEqual(window.audio, audio(0) + audio(1) + audio(2))
        self.assertEqual((window.start_sequence, window.end_sequence), (0, 2))

    def test_reconnect_preserves_expected_sequence(self):
        registry = PcmIngestionRegistry(max_buffer_seconds=2)
        first = registry.register_connection("alpha")
        registry.ingest("alpha", metadata("alpha", 0), audio(0))
        second = registry.register_connection("alpha")
        self.assertEqual(first["reconnect_count"], 0)
        self.assertEqual(second["reconnect_count"], 1)
        self.assertEqual(registry.expected_sequence("alpha"), 1)
        registry.ingest("alpha", metadata("alpha", 2), audio(2))
        registry.ingest("alpha", metadata("alpha", 1), audio(1))
        window = registry.take_audio("alpha", target_duration_ms=600)
        self.assertEqual(window.audio, audio(0) + audio(1) + audio(2))

    def test_backpressure_rejects_without_exceeding_bound(self):
        registry = PcmIngestionRegistry(max_buffer_seconds=0.2)
        accepted = registry.ingest("alpha", metadata("alpha", 0), audio(0))
        rejected = registry.ingest("alpha", metadata("alpha", 1), audio(1))
        self.assertEqual(accepted.status, "accepted")
        self.assertEqual(rejected.status, "backpressure")
        self.assertEqual(rejected.metrics["buffer_depth_bytes"], len(audio(0)))
        self.assertEqual(rejected.metrics["backpressure_rejections"], 1)

    def test_sessions_are_isolated(self):
        registry = PcmIngestionRegistry(max_buffer_seconds=2)
        registry.ingest("alpha", metadata("alpha", 0), audio(1))
        registry.ingest("bravo", metadata("bravo", 0), audio(2))
        alpha = registry.take_audio("alpha", target_duration_ms=200)
        bravo = registry.take_audio("bravo", target_duration_ms=200)
        self.assertEqual(alpha.audio, audio(1))
        self.assertEqual(bravo.audio, audio(2))
        self.assertNotEqual(alpha.audio, bravo.audio)

    def test_metadata_rejects_non_normalized_audio(self):
        invalid = metadata("alpha", 0)
        invalid = PcmChunkMetadata(
            **{**invalid.__dict__, "sample_rate": 48_000}
        )
        with self.assertRaises(PcmProtocolError):
            invalid.validate()

    def test_session_id_mismatch_is_rejected(self):
        registry = PcmIngestionRegistry(max_buffer_seconds=2)
        with self.assertRaises(PcmProtocolError):
            registry.ingest("alpha", metadata("bravo", 0), audio(0))


if __name__ == "__main__":
    unittest.main()

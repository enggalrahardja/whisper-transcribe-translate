import hashlib
import wave
from io import BytesIO
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from app.services.pcm_ingestion import PcmAudioWindow
from app.services.live_processor import LiveChunkDetail
from app.services.pcm_transcription import (
    pcm_window_to_wav,
    transcribe_pcm_window,
    transcribe_pcm_window_detailed,
)


class PcmTranscriptionBridgeTests(TestCase):
    def test_pcm_window_is_normalized_mono_16khz_wav(self):
        window = PcmAudioWindow(
            audio=b"\x00\x00" * 3200,
            start_sequence=4,
            end_sequence=4,
            duration_ms=200,
        )
        encoded = pcm_window_to_wav(window)
        with wave.open(BytesIO(encoded), "rb") as wav_file:
            self.assertEqual(wav_file.getnchannels(), 1)
            self.assertEqual(wav_file.getframerate(), 16_000)
            self.assertEqual(wav_file.getsampwidth(), 2)
            self.assertEqual(wav_file.getnframes(), 3200)

    @patch("app.services.pcm_transcription.process_live_chunk")
    def test_bridge_uses_sequence_identity_without_changing_legacy_signature(self, process):
        process.return_value = ("session", False)
        window = PcmAudioWindow(
            audio=b"\x00\x00" * 3200,
            start_sequence=7,
            end_sequence=9,
            duration_ms=600,
        )
        self.assertEqual(transcribe_pcm_window("session-a", window), ("session", False))
        _, kwargs = process.call_args
        self.assertEqual(kwargs["chunk_identity"], "pcm16:session-a:7:9")

    @patch("app.services.pcm_transcription.process_live_chunk_detailed")
    def test_detailed_bridge_exposes_semantic_segment_contract(self, process):
        identity = "pcm16:session-a:7:9"
        prefix = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:12]
        process.return_value = (
            SimpleNamespace(
                segments=[
                    {"id": f"{prefix}:0", "start": 1.4, "end": 1.9, "text": "hello"},
                    {"id": "another:0", "start": 0, "end": 1, "text": "old"},
                ],
                duration=2.0,
                language="en",
                model="base",
            ),
            False,
            LiveChunkDetail(
                raw_text="hello",
                corrected_text="hello",
                corrections=(),
                glossary_version=None,
            ),
        )
        window = PcmAudioWindow(
            audio=b"\x00\x00" * 9600,
            start_sequence=7,
            end_sequence=9,
            duration_ms=600,
        )
        result = transcribe_pcm_window_detailed("session-a", window)
        self.assertEqual(result.segment_id, "pcm-7-9")
        self.assertEqual((result.sequence_start, result.sequence_end), (7, 9))
        self.assertEqual(result.text, "hello")
        self.assertEqual((result.start_ms, result.end_ms), (1400, 1900))
        self.assertGreaterEqual(result.latency_ms, 0)

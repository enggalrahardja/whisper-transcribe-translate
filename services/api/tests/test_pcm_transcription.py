import wave
from io import BytesIO
from unittest import TestCase
from unittest.mock import patch

from app.services.pcm_ingestion import PcmAudioWindow
from app.services.pcm_transcription import pcm_window_to_wav, transcribe_pcm_window


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

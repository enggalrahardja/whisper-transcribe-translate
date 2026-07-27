import unittest

from app.services.pcm_ingestion import PcmAudioWindow, PcmIngestionRegistry
from app.services.vad import (
    VAD_FRAME_BYTES,
    VAD_FRAME_DURATION_MS,
    VadConfig,
    VadSession,
    VadSessionRegistry,
    VadState,
    WebRtcSpeechDetector,
)


class FakeDetector:
    def __init__(self, decisions):
        self.decisions = iter(decisions)

    def is_speech(self, frame: bytes, sample_rate: int) -> bool:
        self.asserted_sample_rate = sample_rate
        return next(self.decisions)


def frame_audio(index: int) -> bytes:
    value = (index % 32767).to_bytes(2, "little", signed=True)
    return value * (VAD_FRAME_BYTES // 2)


def window(decisions, *, sequence: int = 0, start_index: int = 0) -> PcmAudioWindow:
    audio = b"".join(frame_audio(start_index + index) for index in range(len(decisions)))
    return PcmAudioWindow(
        audio=audio,
        start_sequence=sequence,
        end_sequence=sequence,
        duration_ms=len(decisions) * VAD_FRAME_DURATION_MS,
    )


def config(**overrides) -> VadConfig:
    values = {
        "speech_threshold": 0.6,
        "silence_duration_ms": 600,
        "pre_speech_duration_ms": 300,
        "minimum_speech_duration_ms": 250,
        "maximum_segment_duration_ms": 20_000,
        "segment_overlap_ms": 500,
    }
    values.update(overrides)
    return VadConfig(**values)


class VadSessionTests(unittest.TestCase):
    def test_local_webrtc_detector_rejects_silence_frame(self):
        detector = WebRtcSpeechDetector(mode=2)
        self.assertFalse(detector.is_speech(b"\x00" * VAD_FRAME_BYTES, 16_000))

    def test_pre_speech_buffer_preserves_beginning_of_word(self):
        decisions = [False] * 30 + [True] * 30 + [False] * 70
        session = VadSession(config(), FakeDetector(decisions))
        result = session.process(window(decisions))
        self.assertEqual(len(result.segments), 1)
        segment_audio = result.segments[0].window.audio
        first_speech = b"".join(frame_audio(index) for index in range(30, 60))
        self.assertIn(first_speech, segment_audio)
        self.assertGreaterEqual(result.segments[0].window.duration_ms, 550)

    def test_silence_is_not_emitted_for_transcription(self):
        decisions = [False] * 100
        session = VadSession(config(), FakeDetector(decisions))
        result = session.process(window(decisions))
        flushed = session.flush()
        self.assertEqual(result.segments, [])
        self.assertEqual(flushed.segments, [])
        self.assertEqual(flushed.metrics["speech_segments"], 0)
        self.assertGreaterEqual(flushed.metrics["silence_duration_skipped_ms"], 1000)

    def test_short_noise_is_rejected(self):
        decisions = [False] * 30 + [True] * 20 + [False] * 70
        session = VadSession(config(), FakeDetector(decisions))
        result = session.process(window(decisions))
        self.assertEqual(result.segments, [])
        self.assertEqual(result.metrics["rejected_short_segments"], 1)

    def test_silence_separates_two_speech_segments(self):
        decisions = [True] * 30 + [False] * 70 + [True] * 30 + [False] * 70
        session = VadSession(config(), FakeDetector(decisions))
        result = session.process(window(decisions))
        self.assertEqual(len(result.segments), 2)
        self.assertTrue(all(segment.reason == "silence" for segment in result.segments))
        self.assertEqual(result.metrics["speech_segments"], 2)

    def test_maximum_duration_forces_finalization_with_overlap(self):
        decisions = [True] * 150 + [False] * 70
        session = VadSession(
            config(maximum_segment_duration_ms=1000, segment_overlap_ms=200),
            FakeDetector(decisions),
        )
        result = session.process(window(decisions))
        self.assertGreaterEqual(len(result.segments), 2)
        first, second = result.segments[:2]
        overlap_bytes = 200 * 16_000 * 2 // 1000
        self.assertTrue(first.forced)
        self.assertEqual(first.reason, "maximum_duration")
        self.assertLessEqual(first.window.duration_ms, 1000)
        self.assertEqual(
            second.window.audio[:overlap_bytes],
            first.window.audio[-overlap_bytes:],
        )
        self.assertGreaterEqual(result.metrics["forced_segment_finalization"], 1)

    def test_state_machine_exposes_all_live_states(self):
        detector = FakeDetector([True, True] + [False] * 60)
        session = VadSession(config(speech_threshold=1.0), detector)
        started = session.process(window([True], sequence=0))
        active = session.process(window([True], sequence=1, start_index=1))
        ended = session.process(window([False] * 60, sequence=2, start_index=2))
        self.assertEqual(started.state, VadState.SPEECH_STARTED)
        self.assertEqual(active.state, VadState.SPEECH_ACTIVE)
        self.assertEqual(ended.state, VadState.SPEECH_ENDED)

    def test_reconnect_reuses_session_without_mixing_state(self):
        decisions = [True] * 30 + [False] * 70
        registry = VadSessionRegistry(config(), lambda: FakeDetector(decisions))
        before = registry.session("alpha")
        registry.process("alpha", window([True] * 10, sequence=0))
        after = registry.session("alpha")
        bravo = registry.session("bravo")
        self.assertIs(before, after)
        self.assertIsNot(after, bravo)
        self.assertEqual(bravo.state, VadState.IDLE)

    def test_pcm_buffer_limit_still_applies_before_vad(self):
        registry = PcmIngestionRegistry(max_buffer_seconds=0.2)
        self.assertEqual(registry.max_buffer_bytes, 6400)


if __name__ == "__main__":
    unittest.main()

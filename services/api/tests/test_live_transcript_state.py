import unittest

from app.config import Settings
from app.models.live import CreateLiveSessionRequest
from app.services.live_transcript_state import (
    LiveTranscriptStateRegistry,
    LiveTranscriptUpdate,
    TranscriptState,
)


def update(
    *,
    session_id: str = "session-a",
    segment_id: str = "segment-1",
    revision: int = 1,
    state: TranscriptState = TranscriptState.PARTIAL,
    text: str = "hello",
) -> LiveTranscriptUpdate:
    return LiveTranscriptUpdate(
        session_id=session_id,
        segment_id=segment_id,
        revision=revision,
        state=state,
        sequence_start=2,
        sequence_end=5,
        start_ms=400,
        end_ms=1_200,
        text=text,
        language="en",
        model="base",
        latency_ms=125,
    )


class LiveTranscriptStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = LiveTranscriptStateRegistry(max_sessions=4)

    def test_revision_is_monotonic_and_out_of_order_is_rejected(self):
        self.assertTrue(self.registry.apply(update()).accepted)
        outcome = self.registry.apply(update(revision=3, text="hello there"))
        self.assertFalse(outcome.accepted)
        self.assertEqual(outcome.reason, "out_of_order")
        self.assertEqual(self.registry.metrics("session-a")["rejected_out_of_order"], 1)

    def test_duplicate_update_is_discarded(self):
        candidate = update()
        self.registry.apply(candidate)
        outcome = self.registry.apply(candidate)
        self.assertFalse(outcome.accepted)
        self.assertEqual(outcome.reason, "duplicate")
        self.assertEqual(self.registry.metrics("session-a")["discarded_duplicate"], 1)

    def test_partial_text_can_change(self):
        self.registry.apply(update(text="hel"))
        outcome = self.registry.apply(update(revision=2, text="hello"))
        self.assertTrue(outcome.accepted)
        self.assertEqual(self.registry.snapshot("session-a")[0].text, "hello")

    def test_stable_text_can_grow_but_rollback_requires_reason(self):
        self.registry.apply(update())
        self.registry.apply(update(revision=2, state=TranscriptState.STABLE, text="hello"))
        self.assertTrue(
            self.registry.apply(
                update(revision=3, state=TranscriptState.STABLE, text="hello world")
            ).accepted
        )
        rejected = self.registry.apply(
            update(revision=4, state=TranscriptState.STABLE, text="hello")
        )
        self.assertEqual(rejected.reason, "stable_rollback_requires_reason")
        accepted = self.registry.apply(
            update(revision=4, state=TranscriptState.STABLE, text="hello"),
            rollback_reason="decoder correction",
        )
        self.assertTrue(accepted.accepted)

    def test_final_replaces_working_state_and_locks_segment(self):
        self.registry.apply(update())
        self.registry.apply(update(revision=2, state=TranscriptState.STABLE))
        final = update(revision=3, state=TranscriptState.FINAL)
        self.assertTrue(self.registry.apply(final).accepted)
        self.assertEqual(self.registry.snapshot("session-a"), [final])
        locked = self.registry.apply(update(revision=4, state=TranscriptState.FINAL))
        self.assertFalse(locked.accepted)
        self.assertEqual(locked.reason, "final_immutable")

    def test_reconnect_snapshot_restores_latest_revision(self):
        self.registry.apply(update())
        latest = update(revision=2, state=TranscriptState.STABLE, text="hello again")
        self.registry.apply(latest)
        self.assertEqual(self.registry.snapshot("session-a"), [latest])

    def test_sessions_are_isolated(self):
        self.registry.apply(update(session_id="session-a", text="alpha"))
        self.registry.apply(update(session_id="session-b", text="beta"))
        self.assertEqual(self.registry.snapshot("session-a")[0].text, "alpha")
        self.assertEqual(self.registry.snapshot("session-b")[0].text, "beta")

    def test_metrics_track_latencies_revisions_and_finalization(self):
        self.registry.apply(update())
        self.registry.apply(update(revision=2, state=TranscriptState.STABLE))
        self.registry.apply(update(revision=3, state=TranscriptState.FINAL))
        metrics = self.registry.metrics("session-a")
        self.assertEqual(metrics["partial_latency_ms"], 125)
        self.assertEqual(metrics["stable_latency_ms"], 125)
        self.assertEqual(metrics["final_latency_ms"], 125)
        self.assertEqual(metrics["revisions_per_segment"], {"segment-1": 3})
        self.assertEqual(metrics["finalized_segments"], 1)

    def test_event_contract_contains_every_required_field(self):
        self.assertEqual(
            set(update().as_dict()),
            {
                "sessionId", "segmentId", "revision", "state",
                "sequenceStart", "sequenceEnd", "startMs", "endMs", "text",
                "language", "model", "latencyMs",
            },
        )

    def test_default_flags_preserve_legacy_and_local_base_model(self):
        settings = Settings()
        self.assertFalse(settings.live_pcm_streaming_enabled)
        self.assertFalse(settings.live_vad_enabled)
        self.assertFalse(settings.live_transcript_state_enabled)
        self.assertEqual(CreateLiveSessionRequest().model, "base")


if __name__ == "__main__":
    unittest.main()

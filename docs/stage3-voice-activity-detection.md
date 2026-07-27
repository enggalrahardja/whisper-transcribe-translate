# Stage 3 — Voice activity detection

Status: implemented for the PCM path behind `LIVE_VAD_ENABLED=false`. Legacy
WAV live capture remains unchanged and is still the development default.
Microphone end-to-end validation remains pending.

## Local detector

Stage 3 uses `webrtcvad-wheels==2.0.14`, an in-process build of WebRTC VAD. It
does not call a cloud service or add a transcription provider. WebRTC VAD
accepts 16-bit mono PCM at 16 kHz and 10/20/30 ms frames; the implementation
uses 10 ms frames so all configured timing boundaries are deterministic.

References:

- https://github.com/wiseman/py-webrtcvad
- https://pypi.org/project/webrtcvad-wheels/

WebRTC emits a binary speech decision rather than a probability. The configured
`speech threshold` is therefore the required ratio of speech-positive decisions
inside a rolling 100 ms window.

## PCM + VAD flow

```text
AudioWorklet PCM16 chunks (200 ms)
  -> PCM sequence validation and immediate ACK
  -> bounded contiguous PCM runtime buffer
  -> 10 ms local WebRTC speech detection
  -> per-session pre-speech/speech/trailing-silence buffers
  -> silence, maximum-duration, or stop finalization
  -> minimum-duration rejection
  -> finalized speech segment with optional forced overlap
  -> existing local Whisper bridge
```

ACK and sequence metrics are emitted before the asynchronous consumer runs.
VAD latency or Whisper latency therefore does not delay ingestion ACK creation.
Binary legacy WAV frames bypass this entire VAD path.

## Per-session states

- `idle`: only the bounded pre-speech ring is retained.
- `speech_started`: speech trigger occurred and the pre-speech ring was attached.
- `speech_active`: the segment is collecting speech and brief internal pauses.
- `speech_ended`: the segment was finalized or rejected; the next frame returns
  the state machine to idle unless forced overlap continues active speech.

The VAD registry is keyed by live session ID. Reconnecting returns the same VAD
session state; another session receives an independent detector and buffers.
State is in-process and is not recoverable after an API process restart.

## Parameters and defaults

| Environment variable | Default | Meaning |
|---|---:|---|
| `LIVE_VAD_ENABLED` | `false` | Enables VAD only for PCM consumers |
| `LIVE_VAD_SPEECH_THRESHOLD` | `0.6` | Positive-frame ratio in the 100 ms decision window |
| `LIVE_VAD_SILENCE_DURATION_MS` | `600` | Consecutive detected silence required to finalize |
| `LIVE_VAD_PRE_SPEECH_DURATION_MS` | `300` | Audio retained before the trigger |
| `LIVE_VAD_MINIMUM_SPEECH_DURATION_MS` | `250` | Raw speech required to accept a segment |
| `LIVE_VAD_MAXIMUM_SEGMENT_DURATION_MS` | `20000` | Hard segment duration bound |
| `LIVE_VAD_SEGMENT_OVERLAP_MS` | `500` | Audio copied into the next forced segment |
| `LIVE_VAD_WEBRTC_MODE` | `2` | WebRTC aggressiveness, from 0 to 3 |

## Buffering and finalization behavior

- The 300 ms pre-speech ring preserves speech frames that occur while the
  rolling detector is reaching its threshold.
- Endpoint silence is removed and is not sent to Whisper.
- Pure-silence input never produces a transcription segment.
- Brief pauses shorter than 600 ms stay inside a speech segment to avoid unsafe
  sentence fragmentation.
- Speech shorter than 250 ms is rejected and increments the short-segment
  metric.
- At 20 seconds, finalization is forced and the last 500 ms seeds the next
  segment. This overlap is intentional and uses the existing transcript merge
  behavior downstream.
- The PCM ingestion buffer remains bounded independently. The VAD speech buffer
  is bounded by maximum segment duration; the pre-speech buffer and incomplete
  10 ms frame are also bounded.

## Runtime metrics

`vad_state` WebSocket events expose:

- speech segments;
- rejected short segments;
- silence duration skipped;
- speech duration processed/sent to transcription;
- forced segment finalizations;
- average accepted segment duration;
- average per-frame VAD processing latency.

These metrics and states remain runtime-only; no MongoDB schema was changed.

## Limitations and validation status

- WebRTC VAD has no probability/confidence output and may need threshold/mode
  tuning for far-field rooms, music, and non-stationary noise.
- Pre-speech context and brief internal pauses deliberately include small
  amounts of non-speech audio; standalone and endpoint silence are excluded.
- State survives WebSocket reconnect only while the same API process owns the
  session. Production remains configured for one API worker.
- Automated deterministic state/segmentation tests and a real WebRTC silence
  frame test pass. Interactive microphone E2E, acoustic corpus tuning, and
  supported-hardware latency validation remain pending, so both PCM and VAD
  remain disabled by default.

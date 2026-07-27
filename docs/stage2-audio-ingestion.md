# Stage 2 — Audio ingestion foundation

Status: implemented behind feature flags; legacy remains the development
default until the PCM path is validated with browsers and deployment hardware.

## Internal audio contract

Live PCM ingestion accepts only:

- signed PCM16 little-endian;
- mono;
- 16,000 Hz;
- chunks between 100 and 250 ms (the browser emits 200 ms);
- metadata followed immediately by one raw binary WebSocket frame.

Uploaded WAV, MP3, OGG, FLAC, M4A, and existing video containers remain
compatible. Upload normalization is unchanged: ffmpeg decodes, downmixes, and
resamples media to mono 16 kHz inside the existing Whisper audio loader.

## Live component boundaries

```text
getUserMedia
  -> AudioWorklet capture + resample + Float32-to-PCM16
  -> PCM WebSocket transport (sequence/pending/reconnect)
  -> WebSocket protocol parser + sequence ACK
  -> bounded per-session runtime registry/pre-buffer
  -> asynchronous PCM window consumer
  -> WAV boundary adapter
  -> unchanged local Whisper transcription
```

Capture lives in `apps/web/app/live/pcm-capture.ts` and the worklet processor.
Transport/retry state lives in `pcm-transport.ts`. Server buffering and sequence
state live in `pcm_ingestion.py`; transcription bridging is separate in
`pcm_transcription.py`. No model/provider code was added.

## WebSocket protocol

After the existing `connected` event, a PCM client sends:

```json
{"type":"pcm_hello","sessionId":"..."}
```

The server responds with `pcm_ready`, including its `expectedSequence`, format
constraints, and current metrics. Each chunk then consists of:

```json
{
  "type": "pcm_chunk",
  "sessionId": "...",
  "sequence": 0,
  "captureTimestampMs": 1720000000000,
  "sampleRate": 16000,
  "channelCount": 1,
  "chunkDurationMs": 200,
  "byteLength": 6400
}
```

followed by exactly 6,400 raw PCM bytes. The server returns an `ack` containing
the sequence, status (`accepted`, `duplicate`, `out_of_order`, or
`backpressure`), next expected sequence, current missing sequences, and
metrics. Binary messages without a pending PCM header remain legacy WAV chunks.

## Ordering and reconnect behavior

- Contiguous chunks move into the transcription-ready queue.
- Future chunks are retained out of order within the bounded buffer; current
  missing sequences are exposed separately and the lost metric counts detected
  gaps cumulatively even when a late chunk recovers one.
- Repeated or already-contiguous sequences are acknowledged as duplicates and
  never appended twice.
- Runtime sequence state survives WebSocket reconnects for the lifetime of the
  API process. `pcm_ready.expectedSequence` lets the client reconcile and resend
  unacknowledged chunks.
- State is isolated by live `sessionId`. It is removed when the session stops.
- State is intentionally in-process in Stage 2; multi-worker handoff and process
  restart recovery are not guaranteed.

## Bounds and backpressure

The default server limit is 10 seconds of PCM per session, 128 runtime
sessions, and a maximum forward gap of 128 sequences. A full buffer produces a
`backpressure` acknowledgement without accepting audio. The browser retains up
to 64 unacknowledged chunks and retries backpressured sequences. PCM inference
windows default to 3 seconds and are consumed separately from acknowledgement.

## Metrics

ACK/ready events expose chunks sent/observed, acknowledged, detected lost,
duplicate, out of order, reconnect count, accepted audio duration, buffer depth
(chunks/bytes/ms), and backpressure rejections. The PCM UI displays the primary
metrics. These are runtime metrics and do not change the MongoDB schema.

## Feature flags

Server:

```text
LIVE_PCM_STREAMING_ENABLED=false
LIVE_PCM_MAX_BUFFER_SECONDS=10
LIVE_PCM_TRANSCRIPTION_WINDOW_SECONDS=3
LIVE_PCM_MAX_SESSIONS=128
LIVE_PCM_MAX_SEQUENCE_GAP=128
```

Browser build:

```text
NEXT_PUBLIC_LIVE_AUDIO_TRANSPORT=legacy
```

Set both the server flag to `true` and browser transport to `pcm` for PCM
validation. Leaving defaults unchanged preserves the legacy 3-second WAV path
and local `base` model.

## Explicit non-goals

Stage 2 adds no VAD, diarization, OpenAI integration, cloud provider, model, or
production schema. VAD/endpointing and durable distributed ingestion belong to
later stages.

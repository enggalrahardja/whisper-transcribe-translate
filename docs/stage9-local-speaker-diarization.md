# Stage 9 — local speaker diarization

## Status and scope

Stage 9 is implemented behind `LIVE_DIARIZATION_ENABLED=false` and
`NEXT_PUBLIC_LIVE_DIARIZATION_ENABLED=false`. It consumes only complete final
PCM + VAD audio segments. Enqueueing is non-blocking, so embedding and
clustering do not delay live transcription or translation. The legacy recorder
does not enter this pipeline.

The default configurable model is
`speechbrain/spkrec-ecapa-voxceleb`, a local SpeechBrain ECAPA-TDNN speaker
embedding model under Apache-2.0. The downloaded Hugging Face commit is recorded
as the exact checkpoint. CPU and CUDA are supported; `auto` chooses CUDA when
available. The model has no per-request fee, but download, storage, hardware,
electricity, and operations remain infrastructure costs.

Accuracy is **Unclassified** until the internal multiple-speaker benchmark and
microphone E2E validation are run. Published model-card results are upstream
reference data, not an application benchmark.

## Pipeline

```text
complete VAD segment audio (PCM16 mono 16 kHz)
  -> bounded diarization queue
  -> persistent local speaker embedding
  -> session-local online cosine clustering
  -> speaker assignment overlay
  -> diarization_state event / reconnect snapshot
  -> optional session-wide speaker rename
```

Embedding, clustering, assignment, and rename are separate runtime components.
The assignment registry is separate from transcript, translation, quality, and
audio timestamps. A completed assignment can update an existing `segmentId`
without rewriting any of those source values.

## Identity and confidence

Clusters receive deterministic session-local IDs in discovery order:
`speaker-1`, `speaker-2`, and so on. Their initial labels are `Speaker 1`,
`Speaker 2`, and so on. A centroid is updated after each accepted embedding;
the configured cosine threshold controls whether a segment joins the closest
cluster or creates a new one.

Confidence is the closest cosine similarity normalized to `[0, 1]`; the first
segment of a new cluster receives `1.0` because there is no competing enrolled
speaker. It is a clustering score, not a calibrated probability. Assignments
below `LIVE_DIARIZATION_LOW_CONFIDENCE_THRESHOLD` remain assigned but increment
the low-confidence metric.

Speaker rename updates the label on all assignment overlays in that session and
the cluster label used by future segments. It never changes the stable speaker
ID. Reconnect returns the current mapping and all session assignments. Runtime
state is process-local, so a server restart does not restore it from durable
storage.

## Job lifecycle and failure behavior

Jobs are idempotent by session and segment. Duplicate enqueue returns the
existing job and cannot produce a new assignment revision. Queue capacity,
worker concurrency, retry count, and timeout are bounded. A model error,
timeout, or exhausted retry produces `failed` with no assignment; the original
segment, transcript, translation, and timestamps remain available.

The stored metadata includes provider, model, exact checkpoint, local/cloud,
effective device and compute type, speaker ID and label, confidence, embedding
version, clustering revision, latency, audio boundaries, and creation/update
timestamps.

## Configuration

| Variable | Default |
|---|---|
| `LIVE_DIARIZATION_ENABLED` | `false` |
| `NEXT_PUBLIC_LIVE_DIARIZATION_ENABLED` | `false` |
| `LIVE_DIARIZATION_MODEL` | `speechbrain/spkrec-ecapa-voxceleb` |
| `LIVE_DIARIZATION_MODEL_REVISION` | `main` |
| `LIVE_DIARIZATION_DEVICE` | `auto` |
| `LIVE_DIARIZATION_COMPUTE_TYPE` | `auto` |
| `LIVE_DIARIZATION_SIMILARITY_THRESHOLD` | `0.72` |
| `LIVE_DIARIZATION_LOW_CONFIDENCE_THRESHOLD` | `0.65` |
| `LIVE_DIARIZATION_TIMEOUT_SECONDS` | `30` |
| `LIVE_DIARIZATION_MAX_RETRIES` | `1` |
| `LIVE_DIARIZATION_WORKER_CONCURRENCY` | `1` |
| `LIVE_DIARIZATION_QUEUE_CAPACITY` | `64` |

Stage 9 also requires PCM transport, VAD, and semantic live transcript state.
All remain opt-in; development defaults continue to use the legacy path and
local Whisper `base`.

## Metrics

Runtime metrics expose diarization jobs, detected speakers, assigned and
unassigned segments, low-confidence assignments, retries, failures, average
processing latency, queue depth, and speaker rename count. Model load latency
is also retained for diagnostics.

## Limitations and validation status

- Online clustering is session-local and order-dependent; it is not an offline
  global re-clustering pass.
- Each VAD segment receives one dominant speaker. Overlapping speech is not
  separated and can reduce embedding and assignment accuracy.
- Very short speech, noise, far-field microphones, and acoustically similar
  speakers remain unbenchmarked internally.
- Rename and mappings survive WebSocket reconnect within the API process, but
  not process restart.
- Automated tests cover single/multiple speakers, stable mappings, duplicate,
  retry, timeout, isolation, reconnect, rename, fallback, invariants, bounded
  execution, persistent loading, and feature-off behavior.
- A CPU smoke loaded exact checkpoint
  `0f99f2d0ebe89ac095bcc5903c4dd8f72b367286` and produced a 192-dimensional
  embedding from synthetic PCM16 audio. Microphone E2E remains pending; no
  accuracy claim is made from synthetic input.

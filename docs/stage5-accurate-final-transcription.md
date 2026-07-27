# Stage 5: Accurate final transcription path

Status: implemented as a local, runtime-only queue behind
`LIVE_ACCURATE_FINAL_ENABLED=false`. PCM, VAD, and semantic live-state flags
must also be enabled. Legacy live transcription is unchanged.

## Live versus accurate-final flow

```text
ordered PCM -> local VAD -> complete speech segment
                         |-> live local Whisper -> partial/stable/live-final r3
                         \-> bounded final queue -> accurate local Whisper
                                                -> accurate-final r4 replacement
```

Live transcription continues to use the session's existing local model and the
development default remains `base`. The final worker receives a separate WAV
copy of the complete VAD speech segment. It never consumes a partial live text
as audio input.

## Queue lifecycle

A deterministic key of `sessionId:segmentId` identifies each job. The first
enqueue creates `pending`; a worker changes it to `processing`, then either
`completed` or `failed`. Retriable processing errors return to `pending` before
the next bounded attempt. Enqueuing the same key again returns the existing job
and does not invoke Whisper or create a transcript revision.

The queue capacity, maximum retry count, timeout, and worker count are bounded.
Workers are asyncio tasks and blocking local inference runs in worker threads.
The queue is process-local and is closed during API shutdown.

## Final replacement and failure behavior

Normal Stage 4 final events remain immutable. Only a `completed` accurate-final
job can use the controlled replacement operation. It writes the next revision
for the same `segmentId`, remains in `final` state, and replaces the live text
in the UI. A failed/timeout/full-queue job publishes correction status and
error information but never changes or removes the live result.

WebSocket events:

- `final_correction`: job status, attempt, error/result metadata, queue metrics,
  and a replacement `update` only after successful correction;
- `final_correction_snapshot`: latest jobs and metrics for reconnect.

Accurate-final jobs and metadata remain runtime-only; no MongoDB schema was
changed.

## Model configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `LIVE_ACCURATE_FINAL_ENABLED` | `false` | Enables final reprocessing after semantic live-final |
| `LIVE_FINAL_MODEL` | `base` | Existing downloaded local Whisper checkpoint |
| `LIVE_FINAL_DEVICE` | `auto` | `auto`, `cpu`, or NVIDIA `cuda` |
| `LIVE_FINAL_COMPUTE_TYPE` | `auto` | `auto`, `float16`, or `float32`; float16 requires CUDA |
| `LIVE_FINAL_BEAM_SIZE` | `5` | Beam search width, 1-20 |
| `LIVE_FINAL_TIMEOUT_SECONDS` | `30` | Cooperative per-attempt deadline |
| `LIVE_FINAL_MAX_RETRIES` | `1` | Additional attempts after the initial attempt, 0-10 |
| `LIVE_FINAL_WORKER_CONCURRENCY` | `1` | Fixed process-local workers, 1-8 |
| `LIVE_FINAL_QUEUE_CAPACITY` | `128` | Maximum waiting jobs |

The final transcriber owns one persistent `WhisperAdapter`. The verified local
checkpoint/model is loaded once and reused; configured workers share that model
cache. No OpenAI/cloud provider, translation, or diarization path is added.

## Metadata contract

Every completed result retains:

- exact model name;
- verified checkpoint path and SHA-256;
- effective CPU/CUDA device and float16/float32 compute type;
- detected/requested language and beam size;
- segment timestamps and text;
- accurate-final inference latency.

The job record additionally retains job ID, session/segment IDs, status,
attempt, queued/started/completed timestamps, error, and correction text.

## Metrics

Runtime queue metrics include queued final jobs, average processing latency,
completed, failed, retries, timeout count, queue depth, model load time, and
final replacement count.

## Limitations and E2E status

- Timeout cancellation is cooperative through the existing Whisper decoder
  callback; native operations that do not return promptly cannot be forcibly
  killed safely.
- State, queued audio, jobs, and metadata are in one API process and do not
  survive restart or multi-worker routing.
- A session finishing while its last final job is still processing may close
  the WebSocket before that late status is displayed; runtime state is retained
  while the process owns it.
- Automated queue, idempotency, retry, timeout, isolation, concurrency,
  persistent loader, replacement, failure preservation, metadata, and
  default-off tests pass. Interactive microphone E2E and supported CPU/NVIDIA
  performance validation remain pending, so the feature remains off by default.

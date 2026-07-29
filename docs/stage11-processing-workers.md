# Stage 11 — processing worker architecture

## Topology

```text
PCM/VAD -> live_transcription worker (priority 0)
final segment -> accurate_final_transcription queue (priority class 10)
final/stable text -> translation queue (priority class 20)
completed translation -> translation_quality queue (priority class 40)
final audio -> diarization queue (priority class 30)
final transcript -> transcript_postprocessing queue (priority class 40)
```

Live transcription now executes through its own reusable bounded worker rather
than directly awaiting a generic thread. Existing specialized queues remain
separate execution and model-ownership boundaries. A final/model failure cannot
stop the live worker or another specialized queue. Persistent model loaders
remain lazy and process-scoped.

The implementation is local-only, preserves every existing feature flag and
the legacy default, and changes no production schema or provider.

## Shared contract and lifecycle

`ProcessingJob` supplies `jobId`, `jobType`, `sessionId`, `segmentId`,
`revision`, `status`, `priority`, `attempt`, `maxRetries`, `timeoutMs`,
`createdAt`, `startedAt`, `completedAt`, and `error`. Status is `pending`,
`processing`, `completed`, `failed`, or `cancelled`.

Jobs are idempotent by `jobId`. The priority queue is stable within equal
priority. Only `RetryableJobError` and timeout may retry; permanent and unknown
errors fail immediately. Results and waiter state are process-local.

## Backpressure, cancellation, and shutdown

Every worker has configured capacity and concurrency. Submission to a full
queue raises explicit `WorkerBackpressureError`; the PCM route reports a
retryable backpressure event instead of silently buffering. Session cancellation
marks pending jobs cancelled and cancels active async work safely. The live
session cleanup invokes cancellation after its ordered audio drain.

Startup starts the lightweight live worker and initializes enabled specialized
queues without loading their models. Graceful shutdown first stops accepting
new shared jobs, drains active/pending live work, closes specialized workers,
and removes PCM, VAD, semantic-state, and captured glossary runtime buffers.

## Health and readiness

`GET /api/live/workers/health` returns overall readiness and a record for:

- `live_transcription`
- `accurate_final_transcription`
- `translation`
- `translation_quality`
- `diarization`
- `transcript_postprocessing`

The shared worker exposes running/readiness, queue depth/capacity, active jobs,
model loaded, last success/failure, completed, failed, retried, cancelled,
queue-full rejection, average wait/processing time, model-load time, and restart
count. Specialized adapters expose the corresponding metrics available from
their established queues. A disabled feature is readiness-neutral.

## Priorities and limitations

Priority classes are live, final transcription, translation, diarization, then
post-processing. They apply inside a worker queue; isolation between workers is
what guarantees that a final job never occupies live worker capacity.

The queues are still in-process and not durable. Process failure loses pending
jobs and runtime health history. Active synchronous inference runs in a bounded
thread and cannot always be pre-empted inside native model code; cancellation
prevents its result from being promoted. Multi-process deployments expose
health per process and do not share idempotency state.


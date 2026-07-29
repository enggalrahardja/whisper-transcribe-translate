# Stage 14 — quality monitoring and operational metrics

`GET /api/live/monitoring` is read-only and aggregates ingestion, PCM, VAD,
semantic transcript, accurate-final, glossary, translation/quality,
diarization, post-processing, persistence, worker, model, and resource metrics.
An optional `session_id` returns one isolated session aggregate without its
identifier or transcript/audio content. System output includes active/total
sessions, connections, readiness, queues, jobs, failures/retries, persistence
degradation, CPU/RAM and best-effort GPU/VRAM fields.

Latency summaries use linearly interpolated p50/p95/p99 over available samples;
empty inputs are zero. Quality indicators are ratios with safe denominators:
empty/repeated/low-confidence/untranslated/fallback/replacement/glossary/drop
rates and processing real-time factor. Runtime metrics reset after restart;
session quality snapshots persisted by Stage 12 survive. No external
observability backend is added, but the flat names and units (`Ms`, `Seconds`,
`Percent`, counts, ratios) are export-friendly.

Redaction recursively removes transcript/text/audio fields, IDs, and keys whose
names imply secrets, tokens, passwords, or credentials. Threshold warnings cover
queue utilization (default 80%), processing latency (5000 ms), persistence
degradation, unavailable workers, and failure rate (10%). GPU pressure and audio
drop fields remain contract-ready when collectors provide them.

The `/monitoring` UI shows health, workers, queue/job/error counts, latency and
resources using tables. Refresh defaults to 10 seconds, is configurable, stops
network polling while the tab is hidden, and supports loading, empty, degraded,
and unavailable states.

Manual screenshots: healthy/degraded summary; queue warning; unavailable worker;
resource fields with and without GPU; empty and fetch-error states; narrow table
overflow; keyboard focus; hidden-tab network pause. Known limitations: metrics
are process-local, total sessions is capped to the latest 100, GPU collection is
best-effort, and no Prometheus/OpenTelemetry exporter exists yet.

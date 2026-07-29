# Stage 12 — session and segment persistence

## Entity relationship

```text
TranscriptionSession 1 ── * AudioSegment
TranscriptionSession 1 ── * TranscriptRevision
AudioSegment         1 ── * TranslationRevision
AudioSegment         1 ── 1 SpeakerAssignment
TranscriptionSession 1 ── * ProcessingJobSummary
```

All entities carry `schemaVersion=1`, `createdAt`, and `updatedAt` so future
migrations and retention policies can be introduced without deleting data now.
Collections use unique session, segment, revision, and job keys. Raw PCM chunks
are never documents. Segment records contain only boundaries, finalization
reason, runtime/file reference, and SHA-256; audio binary remains in the
existing filesystem/storage architecture.

## Revision lifecycle

Semantic live final remains its immutable revision. Accurate-final is inserted
as the next revision instead of overwriting it. When transcript post-processing
is authoritative, it creates another derived revision containing raw,
glossary-corrected, and post-processed values separately. Translation revisions
are likewise append-only and idempotent. Equal duplicate payloads are ignored;
conflicting duplicate or regressing revisions fail.

Speaker assignment is separate from transcript revisions. Rename updates only
`speakerLabel` and `updatedAt` for matching session speaker mappings.

## Runtime versus persisted state

`LIVE_PIPELINE_PERSISTENCE_ENABLED=false` keeps legacy/default behavior.
When enabled, WebSocket processing submits versioned documents to a bounded
write-behind service. Mongo queries and indexes exist only in
`MongoPipelineRepository`; routes never issue collection queries directly.
Runtime processing queues remain in-process and persistence is not a durable
job queue.

Writes retry transient repository errors up to the configured bound. Queue-full
or exhausted writes mark the session degraded and increment metrics without
raising into PCM ingestion or ACK handling. Metrics include attempted,
successful, failed, retry, duplicate, restore count/latency, queue depth, and
degraded sessions.

## Restore behavior

Reconnect prefers the semantic runtime snapshot. If it is unavailable, the
persistence service loads the session aggregate, selects the newest transcript
revision per segment, and returns the normal transcript snapshot contract.
Restore failure yields an empty snapshot and does not close live ingestion.

## Metadata and redaction

Session documents capture an allow-listed feature/configuration snapshot and
hardware platform. Revision metadata retains provider, exact model/checkpoint
when supplied upstream, local/cloud, device, compute type, beam size, language,
glossary version, corrections, and latency. Recursive redaction replaces keys
matching secret, password, token, API key, credential, authorization, or cookie.
Full environment variables and credentials are never copied.

## Configuration and limitations

| Variable | Default |
|---|---|
| `LIVE_PIPELINE_PERSISTENCE_ENABLED` | `false` |
| `LIVE_PIPELINE_PERSISTENCE_QUEUE_CAPACITY` | `256` |
| `LIVE_PIPELINE_PERSISTENCE_MAX_RETRIES` | `2` |

The write queue is non-durable. A process crash can lose accepted writes still
in memory. Audio references for transient live segments are runtime references
until a later stage introduces durable segment audio files. No deletion or TTL
policy is implemented yet. Mongo transactions across entities are not used;
idempotent per-entity writes support bounded recovery.


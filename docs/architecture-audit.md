# Existing architecture audit — Stage 1

## Stage 15 addendum — benchmark evidence and profiles

Stage 15 leaves legacy processing unchanged. The Stage 1 runner now executes a
reviewed-script synthetic dataset and captures model-load time with accuracy,
latency, and resources. `config/model-profiles.json` defines Fast, Balanced,
Accurate, and Private; the resolver verifies checkpoints/CUDA and uses explicit
local fallbacks. No profile auto-activates and no cloud provider was added.
Only `base.pt` was available; all other results remain explicitly `not_run`.

Audit date: 2026-07-27. This document describes implemented behavior, not the
aspirational architecture in `README.md` or `PLAN.md`.

## Stage 16 addendum — production boundary

Stage 16 adds an opt-in development/mandatory production security boundary:
environment bearer principals, owner-scoped sessions, admin operations,
pre-accept WebSocket auth/origin checks, in-process rate/resource limits, strict
PCM frames, sanitized errors/audit records, bounded retention cleanup, security
headers, startup validation, and dependency readiness. The legacy local path
remains the development default and no cloud provider was introduced. TLS,
encryption at rest, shared multi-instance limiting, durable audit export, and
cleanup scheduling remain deployment concerns.

## Implemented processing flows

### Web upload flow (primary headless path)

```text
Browser file input
  -> POST /api/uploads (multipart)
  -> extension, size, and file-signature validation
  -> storage/uploads/<uuid>.<ext> + MongoDB media_files
  -> MongoDB transcription_jobs queue
  -> persistent polling worker
  -> ffmpeg decode/downmix/resample to mono 16 kHz inside Whisper
  -> OpenAI Whisper checkpoint loaded through the repository runtime
  -> transcript segments/text
  -> optional GoogleTranslator text translation over the network
  -> MongoDB transcripts + completed transcription_jobs
  -> REST polling/result pages in Next.js
```

Accepted upload containers are WAV, MP3, OGG, FLAC, M4A, MP4, MOV, WMV, AVI,
and MKV. `src/whisper/audio.py` delegates decoding to ffmpeg and produces mono
16 kHz float audio. Whisper uses a 30-second mel window internally; its
transcription loop handles longer inputs. There is no application-level VAD,
noise suppression, echo cancellation, or gain normalization for uploaded
media.

The worker retains loaded models in a per-worker `WhisperAdapter` cache. The
selected model checkpoint must already be downloaded, checksum-verified, and
registered as available. Progress is a job/UI indicator mapped from Whisper
progress; it is not a latency measurement.

### Web live flow

```text
Browser getUserMedia
  -> Web Audio ScriptProcessor, first channel only
  -> PCM16 mono WAV chunks over WebSocket
  -> temporary chunk file
  -> serialized Whisper inference
  -> word-overlap merge + timestamp offset
  -> MongoDB live_sessions.partial_text/segments
  -> partial WebSocket event to Next.js
  -> stop command copies partial_text to final_text
  -> final/stopped WebSocket events
```

The default live chunk is 3 seconds with 0.5 seconds of retained overlap
(configurable to 2–5 seconds and 0–2 seconds respectively). Capture uses the
browser AudioContext sample rate; it is encoded as mono PCM16 WAV, then ffmpeg
resamples to 16 kHz during model input. Browser `getUserMedia` is requested with
echo cancellation and noise suppression (automatic gain control is not
explicitly requested), but the server has no corresponding preprocessing or
VAD stage.

Each chunk is complete-file Whisper inference, protected by one process-wide
lock. Results called `partial` are append/overlap-merged completed chunk
outputs, not token streaming. There is no implemented `stable` state. On stop,
the current partial text is copied verbatim to `final_text`; no accurate final
reprocessing occurs. Chunk hashes prevent the same byte payload being accepted
twice, while a word-suffix/prefix heuristic tries to remove overlap duplicates.

### Legacy desktop flow

The CustomTkinter entry point remains in `main.py`. File transcription loads a
model from `assets/config/settings.json` (default `base`, CPU), lets ffmpeg
normalize audio inside Whisper, and runs transcription in a UI thread helper.
Translation first transcribes locally and then calls `deep_translator`'s Google
provider. The legacy experimental live recorder captures 5-second, 44.1 kHz,
two-channel WAV files with a hard-coded `base` model and is separate from the
web live flow.

## Models and configuration

- Implemented ASR runtime: the repository's fork/copy of OpenAI Whisper
  (`src/whisper`), backed by PyTorch—not `faster-whisper`.
- Web-supported checkpoints: `tiny`, `base`, `small`, `medium`, and `large`;
  `large` resolves to the `large-v3.pt` checkpoint.
- Web defaults: `base` for file and live transcription in
  `services/api/app/models/settings.py`. Persisted settings live in MongoDB's
  `application_settings` collection and are editable through `/api/settings`.
- Model file root: `WHISPER_MODEL_DIR`, default
  `storage/models/whisper`, defined in `services/api/app/config.py`.
- Trusted URLs, canonical names, and expected SHA-256 values:
  `services/api/app/services/whisper_model_metadata.py` and
  `src/whisper/__init__.py`.
- Device/inference controls: auto/CPU/CUDA, FP16, beam size, temperature,
  prompt, timestamps, and concurrency in the persisted application settings.
- Legacy desktop default: `base`/CPU in `src/logic/settings.py`.
- Legacy translation remains `deep_translator.GoogleTranslator`, configured
  only as `google`. Stage 7 adds a separate optional local Marian runtime for
  semantic PCM stable/final transcripts; it does not alter the legacy route.
- OpenAI API models are mentioned in planning documentation only. There is no
  Stage 1 OpenAI provider integration or credential path.

## Result delivery and storage

Uploaded binaries are stored on the filesystem under `storage/uploads` and
described by MongoDB `media_files`. Jobs, progress/error state, transcripts,
translations, and live sessions are stored in MongoDB collections. Next.js
pages use REST for uploads/history/results and WebSocket for live events. The
legacy desktop path does not share this persistence model.

For file jobs, the stored transcript contains original text/segments and, for a
translation job, translated text. Exact provider version, checkpoint digest,
hardware, inference timings, resource usage, and translation request metadata
are not stored with each result. Live session storage contains model, language,
text, segment timestamps, session timestamps, and errors, but not audio chunks.

## Existing logging and measurements

- Python worker logs lifecycle, completion, cancellation, and exception events
  through standard logging.
- Model downloader and API runtime expose status/heartbeat/progress documents.
- Job `started_at`, `completed_at`, heartbeat, and progress are stored.
- Live sessions store wall-clock start/end and a duration excluding pauses.
- There is no WER/CER, translation-quality metric, partial/stable/final latency,
  real-time factor, CPU/RAM/GPU/VRAM sampling, percentile aggregation,
  structured tracing, or metrics exporter.
- Existing uses of `monotonic()` throttle progress/registry/cache operations;
  they do not measure inference latency.

## Scope preservation

Stage 1 adds only the isolated `benchmarks/` suite and documentation. It does
not change application workflow, UI, production schemas, provider selection,
or runtime behavior. Local Whisper remains the application development default.

## Stage 2 addendum

Stage 2 adds an optional AudioWorklet/PCM16 ingestion path alongside the legacy
WAV path. Its capture, transport, bounded runtime buffer, and transcription
bridge are separate components. Sequence acknowledgements and ingestion metrics
remain WebSocket/runtime state rather than MongoDB fields. See
`docs/stage2-audio-ingestion.md` for the protocol, limits, feature flags, and
known process-lifetime limitation.

## Stage 3 addendum

Stage 3 places a local WebRTC VAD consumer after ordered PCM ingestion and
before the transcription bridge. PCM acknowledgement remains independent of
VAD and Whisper. Detection, bounded pre-speech/speech buffers, segment
finalization, and transcription remain separate responsibilities. The VAD
state and metrics are isolated per session and runtime-only. Legacy live audio
bypasses VAD, while both PCM and VAD feature flags remain disabled by default.
See `docs/stage3-voice-activity-detection.md` for timing defaults, metrics,
limitations, and validation status.

## Stage 4 addendum

Stage 4 adds a bounded, runtime-only semantic result registry after PCM + VAD
segment transcription. Each session/segment retains only its newest accepted
`partial`, `stable`, or immutable `final` revision. WebSocket reconnect returns
that latest snapshot after the PCM handshake. The UI keys results by segment,
so final replaces the same partial/stable entry rather than appending a second
copy. The registry records state latency, revisions, discarded duplicates,
rejected out-of-order updates, and finalized segment counts.

The new backend and frontend flags are both disabled by default. No MongoDB
schema, cloud provider, legacy recorder behavior, or default local `base` model
was changed. Existing Whisper is still window-based rather than true token
streaming; see `docs/stage4-live-transcription-state.md` for the event contract,
revision rules, reconnect behavior, UI semantics, and current validation status.

## Stage 5 addendum

Stage 5 forks each complete VAD speech segment after the live transcription
result: the existing live path returns immediately, while a bounded local queue
can reprocess the same full audio with a separately configurable, persistent
Whisper model runtime. Jobs are idempotent by session/segment, have bounded
retry/timeout/concurrency, and expose pending/processing/completed/failed state.

A completed job performs a controlled next-revision replacement of the same
semantic `segmentId`. Failed jobs preserve the live final. Exact verified
checkpoint SHA-256/path, model, effective device/compute type, language, beam
size, timestamps, latency, job state, and queue metrics remain runtime-only.
The feature is disabled by default; the legacy route, local live `base` default,
MongoDB schemas, translation, diarization, and provider set are unchanged. See
`docs/stage5-accurate-final-transcription.md` for configuration, lifecycle,
failure semantics, metadata, and E2E limitations.

## Stage 6 addendum

Stage 6 adds a local JSON glossary manager in front of both semantic live and
accurate-final inference. Each VAD segment captures one immutable glossary
snapshot: its prompt context is combined with the model prompt and the same
snapshot performs deterministic whole-word post-correction on raw output.
Accurate-final receives that snapshot with the segment audio, so a runtime
reload applies only to later segments and never reloads Whisper.

Runtime events retain raw and corrected text, glossary version, and applied
corrections without changing audio timestamps. Matching is case-aware,
boundary-safe, priority-resolved, and idempotent; protected terms block lower
priority overlaps. Metrics remain process-local. The feature flag is off by
default, no production schema changes were introduced, and legacy, translation,
diarization, provider selection, and local `base` defaults are unchanged. See
`docs/stage6-local-glossary.md` for file structure and matching/reload rules.

## Stage 7 addendum

Stage 7 adds a runtime-only local translation consumer after semantic transcript
state. Accepted `stable` source revision creates a preview job; each newer
`final` source revision creates a final job, including the controlled
accurate-final replacement. Translation state is keyed separately by
session/segment and never mutates the source transcript registry.

The bounded queue rejects duplicate and stale source revisions, caps queue
capacity/concurrency/retries/timeout, and retains one lazily loaded Marian
model per API process. Events and reconnect snapshots carry provider,
model/checkpoint, local/cloud, language pair/detection, context IDs, glossary
version, device/compute type, latency, revision, and timestamps. Glossary
terms can protect text from translation or force a preferred target form.

The feature flag is off by default, requires the PCM semantic-state path, and
does not change MongoDB schemas, the legacy recorder, Whisper `base`, or any
cloud provider. See `docs/stage7-local-live-translation.md` for lifecycle,
configuration, limitations, and validation status.

## Stage 8 addendum

Stage 8 adds a separate runtime-only quality consumer after a Stage 7
translation reaches `completed`. Preview translations bypass it. The quality
registry retains raw model translation, the final Stage 7 translation,
corrected translation, applied rules, timestamps, language metadata, and
pending/processing/completed/failed status without modifying the source
transcript or translation preview.

The processor is deterministic rule-based local code: terminology enforcement,
whitespace/punctuation/capitalization normalization, and consecutive repeated
phrase removal. Dates, times, numbers, codes, versions, and speaker labels are
marker-protected while rules run. A postcondition compares digit, negation, and
speaker-attribution invariants; violation or runtime failure falls back to the
unaltered final translation.

Queue capacity, concurrency, retry, and timeout remain bounded. State and
metrics are process-local and reconnectable over WebSocket. Both quality flags
default off; no database schema, legacy behavior, model/provider selection, or
cloud integration changed. See `docs/stage8-final-translation-quality.md`.

## Stage 9 addendum

Stage 9 forks each complete final VAD segment into a bounded local diarization
queue after transcription has already produced its segment result. Enqueueing
returns without waiting for inference, so embedding and clustering do not block
live transcription or translation. Partial PCM chunks and the legacy recorder
never enter this branch.

A persistent local SpeechBrain ECAPA-TDNN runtime creates speaker embeddings.
A separate session-local online clusterer assigns stable discovery-order IDs
(`speaker-1`, `speaker-2`, and so on), while a separate overlay registry stores
assignment metadata and renameable labels. Reconnect snapshots restore the
current process-local assignments and names. Rename applies to all existing and
future assignments in that session.

Jobs are idempotent and queue capacity, concurrency, retries, and timeout are
bounded. Failure leaves the segment unassigned and never mutates transcript,
translation, or timestamps. Both feature flags default off; no production
schema, legacy behavior, Whisper `base` default, provider, or cloud integration
changed. See `docs/stage9-local-speaker-diarization.md`.

## Stage 10 addendum

Stage 10 adds a separate runtime-only post-processing consumer for semantic
final transcript revisions. Live final can enqueue first; an accepted
accurate-final replacement has a higher revision and supersedes it for the same
segment. Partial/stable states and the legacy recorder do not enter this branch.

The bounded local rule queue retains raw model, glossary-corrected, and
post-processed text separately. It normalizes conservative formatting,
optionally handles configured filler words, segments paragraphs, protects
technical/invariant tokens, and validates safety postconditions. Failure falls
back to the glossary-corrected transcript without mutating transcript state,
translation, diarization, or timestamps. Reconnect snapshots return the latest
process-local result.

Both flags default off. No production schema, Whisper model/default, provider,
LLM, or cloud integration changed. See
`docs/stage10-transcript-postprocessing.md`.

## Stage 11 addendum

Stage 11 introduces a reusable shared job contract and bounded priority worker
kernel. PCM/VAD transcription now uses a dedicated live worker; accurate-final,
translation, translation quality, diarization, and transcript post-processing
retain independent specialized queues and lazy persistent model ownership.
This separation prevents heavy final work from consuming live capacity.

Application startup brings up the live worker and initializes enabled queue
boundaries. Graceful shutdown stops acceptance, drains or cancels safely,
closes each worker independently, and clears session PCM/VAD/semantic/glossary
runtime buffers. Session cleanup cancels outstanding live jobs. A consolidated
health/readiness endpoint reports all six workers, queue/backpressure state,
activity, model state, success/failure, and processing metrics.

The architecture remains local-first and schema-neutral. Queue state is still
in-process and non-durable. See `docs/stage11-processing-workers.md`.

## Stage 12 addendum

Stage 12 adds versioned Mongo persistence behind an opt-in write-behind service
and repository boundary. Sessions capture allow-listed feature, configuration,
hardware, quality, and model context. Segment documents retain finalized audio
metadata/reference/SHA-256 but never raw PCM chunks. Immutable transcript and
translation revisions preserve raw and derived values; accurate-final and
post-processing append revisions. Speaker mappings and job summaries remain
separate entities.

Unique indexes and idempotent repository operations reject conflicting or
regressing revisions. Recursive redaction excludes credential-like keys.
Bounded retry and degraded-session metrics isolate persistence failure from PCM
ingestion. Reconnect prefers runtime state and falls back to the newest
persisted revision per segment when runtime state is unavailable.

Persistence remains optional and is not a durable processing queue. No legacy
storage is removed, no raw chunk is stored in Mongo, and no cloud/provider or
production behavior changes while the flag is off. See
`docs/stage12-session-segment-persistence.md`.

## Stage 13 addendum

Stage 13 reorganizes the existing live page around session/device controls,
source and translated transcript, processing status, and metrics. Semantic data
is projected into one keyed block per segment. Pure merge/display precedence
functions make reconnect and revision replacement deterministic, while raw
metadata moves behind expandable details.

Viewport-aware auto-scroll stops when the user reads earlier content and shows
a new-result indicator. Device selection/input level, reconnect, empty, error,
and degraded-persistence states are explicit. Desktop uses paired source and
translation columns; mobile stacks them without adding nested scrolling.
Legacy rendering and all default-off flags remain unchanged. See
`docs/stage13-live-transcription-ui.md`.

## Stage 14 addendum

Stage 14 adds a read-only, content-free aggregation layer and separate
monitoring page. It combines existing runtime/persisted counters, worker health,
resource utilization, percentile latency, quality ratios, and configurable
warning thresholds without affecting processing outputs. Polling pauses while
the browser tab is hidden. Runtime metrics remain non-durable and the contract
is shaped for a later Prometheus/OpenTelemetry exporter. See
`docs/stage14-quality-monitoring.md`.

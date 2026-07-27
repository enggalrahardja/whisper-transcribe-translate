# Existing architecture audit — Stage 1

Audit date: 2026-07-27. This document describes implemented behavior, not the
aspirational architecture in `README.md` or `PLAN.md`.

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
- Implemented translation: `deep_translator.GoogleTranslator`, configured only
  as `google`. It is a cloud/network dependency; no local translation model is
  implemented.
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

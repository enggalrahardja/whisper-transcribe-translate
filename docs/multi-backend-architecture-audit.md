# Multi-Backend Whisper Architecture Audit

## Existing flow

The web upload forms in `apps/web/app/transcribe/page.tsx` and
`apps/web/app/translate/page.tsx` submit multipart fields to the existing
`POST /api/uploads` route. `services/api/app/routes/uploads.py` validates the
request and calls `services/api/app/services/jobs.py`, which stores a document
in the existing `transcription_jobs` collection. `services/api/app/worker.py`
claims that document, resolves its media record and file path, performs
inference, post-processes the normalized result, and stores it through the
existing transcript service/collection. The job detail page polls the existing
`GET /api/jobs/{job_id}` response and then fetches its result.

Application defaults flow through the existing `GET/PATCH /api/settings`
route and the single versioned `application_settings` document. Runtime
capabilities are exposed below that same settings router rather than through a
parallel configuration API.

## Original PyTorch coupling

- `worker.py` constructed `WhisperAdapter` directly and passed the PyTorch-only
  `fp16` flag to both model loading and inference.
- `whisper_adapter.py` imported the bundled `src.whisper` implementation and
  `torch`, resolved only local `.pt` checkpoints, and owned CUDA cleanup.
- Upload/job creation always acquired a local checkpoint usage lease, which is
  valid for PyTorch but not for faster-whisper's CTranslate2/Hugging Face model
  lifecycle.
- Jobs persisted `model` but no backend/device/compute snapshot.
- The upload UI populated its model selector only from
  `/api/settings/models/available`, so it could represent only downloaded
  PyTorch checkpoints.
- Compute selection in persisted settings was represented only by `fp16`.

There were no separate environment variables for the upload-worker backend or
compute type. `LIVE_FINAL_DEVICE` and `LIVE_FINAL_COMPUTE_TYPE` belong to the
separate accurate-final live pipeline and are not reused for upload jobs.

## Generic interface and cache lifecycle

`services/api/app/services/transcription_backends.py` now defines the generic
configuration/options contracts and the common backend operations: load,
transcribe, unload, and runtime metadata. `PytorchWhisperBackend` delegates to
the existing adapter; `FasterWhisperBackend` consumes the faster-whisper
segment generator and normalizes it to the existing result shape.

`TranscriptionBackendManager` is the worker-facing adapter. Its process-wide
lock spans backend switching, loading, inference, and unloading. Its cache key
is `backend:model:device:compute_type`, and switching any component releases
the previous implementation before loading the replacement. This prevents a
PyTorch and CTranslate2 model from remaining active together.

## Result and confidence compatibility

The normalized contract retains `text`, `segments`, segment `start`/`end`,
language, duration, and runtime metadata. faster-whisper segment
`avg_logprob` and `no_speech_prob` are copied without inventing a new score.
The existing post-processing path converts `avg_logprob` to display confidence
as `exp(avg_logprob)`, clamped to `[0, 1]`; that existing formula is shared by
both backends.

The legacy value `large` is retained in stored/API data. It maps explicitly to
`large-v3` for faster-whisper and to the existing local registry key `large`
for the bundled PyTorch implementation.

## Persisted metadata and backward compatibility

New jobs persist `transcription_backend`, `transcription_device`, and
`transcription_compute_type`. Worker model metadata records requested and
active backend/model, requested and effective device/compute type, backend
library version, cache identity/status, model load duration, inference
duration, and available VRAM data from the existing PyTorch adapter.

Old jobs without the new fields use `pytorch`, preserving the original
backend. Their device comes from the existing application setting, and an
explicit legacy `fp16=false` continues to resolve to `float32`. Existing job
and transcript fields remain present and unchanged.

## Files changed by the implementation

- Backend/lifecycle: `transcription_backends.py`, `whisper_adapter.py`,
  `worker.py`, and the API dependency list.
- Configuration/API: settings and job Pydantic models, application settings,
  settings/uploads routes, and job persistence.
- UI: shared API types, Settings, Transcribe, Translate, and job detail pages.
- Validation: backend contract/capability/cache tests, worker OOM regression
  tests, and web selector contract tests.

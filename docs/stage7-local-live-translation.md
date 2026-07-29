# Stage 7 — local live translation

## Status and boundaries

Stage 7 is implemented behind `LIVE_TRANSLATION_ENABLED=false` and
`NEXT_PUBLIC_LIVE_TRANSLATION_ENABLED=false`. It consumes text only from the
optional PCM + VAD + semantic-state path. It does not receive audio, mutate a
source transcript, alter MongoDB schemas, change the legacy live route, change
Whisper's local `base` default, or add an OpenAI/cloud provider.

Automated queue/state/integration tests and one synthetic CPU model/glossary
smoke test are complete. Interactive microphone E2E, translation quality
benchmarking, and CPU/CUDA resource measurements remain pending, so the model
is **Unclassified** and is not labelled “best.”

## Flow

```text
PCM ACK/VAD (independent)
  -> local Whisper semantic transcript registry
       stable revision -> bounded translation preview job
       final revision  -> bounded final translation job
       accurate-final replacement -> newer priority final translation job
  -> persistent local Marian runtime
  -> deterministic translation glossary restoration
  -> runtime translation registry keyed by sessionId/segmentId
  -> translation_state WebSocket event / reconnect snapshot
  -> UI preview or completed translation below unchanged source text
```

`partial` transcripts are never translated. Stable output is `preview`; final
output is `completed` and replaces the preview for the same `segmentId`.
Accurate-final source revision is newer than the live final and therefore
supersedes it. A failed job reports `failed` but does not touch the source
transcript.

## Model and language pair

Development default is `Helsinki-NLP/opus-mt-id-en` through a lazy direct
`AutoTokenizer`/`AutoModelForSeq2SeqLM` Transformers integration. The model
card identifies Marian, Indonesian source, English target, SentencePiece
preprocessing, and Apache-2.0 license. The upstream Tatoeba numbers are not
treated as internal accuracy evidence. See the
[official model card](https://huggingface.co/Helsinki-NLP/opus-mt-id-en).

The provider metadata is `transformers-marian`, locality is `local`, and the
resolved Hugging Face commit hash is retained as the checkpoint when available.
The runtime supports `cpu`, `cuda`, and `auto`, plus `float32`, `float16`, and
`auto` compute selection. Float16 is rejected on CPU. The local model has no
per-request fee; model storage, host/GPU capacity, electricity, and operations
remain infrastructure costs.

The model ID, revision, and source/target pair are configurable. A model must
actually support that configured pair. `source_language=auto` enables a small
local Indonesian/English stop-word heuristic and stores its detected language
and confidence; low-confidence/unknown output is expected for short or
code-switched text.

## Configuration

| Variable | Default |
|---|---|
| `LIVE_TRANSLATION_ENABLED` | `false` |
| `NEXT_PUBLIC_LIVE_TRANSLATION_ENABLED` | `false` |
| `LIVE_TRANSLATION_MODEL` | `Helsinki-NLP/opus-mt-id-en` |
| `LIVE_TRANSLATION_MODEL_REVISION` | `main` |
| `LIVE_TRANSLATION_SOURCE_LANGUAGE` | `id` |
| `LIVE_TRANSLATION_TARGET_LANGUAGE` | `en` |
| `LIVE_TRANSLATION_DEVICE` | `auto` |
| `LIVE_TRANSLATION_COMPUTE_TYPE` | `auto` |
| `LIVE_TRANSLATION_BEAM_SIZE` | `4` |
| `LIVE_TRANSLATION_TIMEOUT_SECONDS` | `20` |
| `LIVE_TRANSLATION_MAX_RETRIES` | `1` |
| `LIVE_TRANSLATION_WORKER_CONCURRENCY` | `1` |
| `LIVE_TRANSLATION_QUEUE_CAPACITY` | `64` |
| `LIVE_TRANSLATION_CONTEXT_SEGMENTS` | `3` |

Backend and frontend flags should be enabled together only after PCM, VAD, and
semantic transcript state have passed E2E validation.

## State and revision rules

Jobs emit `pending`, `processing`, and then `preview`, `completed`, or `failed`.
The idempotency key includes session, segment, source revision/state, and target
language. An identical key is discarded; a lower or conflicting revision is
rejected. Translation revisions increase monotonically per segment. Runtime
snapshots restore the latest job for each segment after reconnect.

Every event separates `sourceText`, `rawTranslatedText`, and `translatedText`.
Metadata includes provider, model/checkpoint, local/cloud, configured/detected
source language, target language, context segment IDs, glossary version,
device, compute type, latency, source revision, detection confidence, and
created/updated timestamps.
Audio `startMs` and `endMs` are carried unchanged from the source transcript so
downstream local quality processing cannot shift segment timing.

## Context and terminology

The request captures a bounded list of preceding source segment IDs/texts.
They are passed with the current source in one local batch and recorded in
metadata. Marian translates batch items independently, so this provides a
bounded runtime context contract and consistent glossary selection but not
true document-level cross-attention.

Active glossary terms may add `doNotTranslate: true` and/or
`preferredTranslations: {"en": "..."}`. Matching remains whole-word,
case-insensitive, and priority-resolved. Terms present in the source are
replaced with deterministic markers before inference and restored afterward;
the source text is never changed. If a model damages or drops a marker, the
raw translation remains available and the term is reported only when restored.

## Metrics and limitations

Runtime metrics cover queued jobs, preview/final latency, completed, failed,
retries, queue depth, model load time, glossary terms applied, final replacement
count, duplicate/stale rejection, and average language-detection confidence.

State is process-local and is lost on API restart. One API worker remains the
safest WebSocket deployment. Queue timeout cannot forcibly stop native model
execution already running in a Python worker thread; the persistent runtime
serializes access so timed-out work cannot overlap inference on the same model.
Marian has a 512-token configured input cap, is pair-specific, is not a token
streaming model, and has not been internally validated for technical meetings,
code-switching, noisy ASR output, glossary marker retention, or long-context
coherence.

# Model catalogue — implemented and recorded options

Accuracy classification uses **Unclassified** until the internal suite has
enabled, reviewed cases and produces results. No model is labelled “best”.

Stage 15 produced the first synthetic internal baseline for `base`: mean WER
0.5278 and CER 0.1852 across 7/7 completed cases on dataset
`stage15-safe-synthetic-benchmark` v1.0.0 (2026-07-29). This is a **synthetic
baseline only**, not a natural-speech rating. Other checkpoints/components were
unavailable and remain unclassified. Full language, licence, privacy, pricing,
hardware, and limitations are in `docs/stage15-internal-benchmark-model-profiles.md`.

## Implemented local transcription models

All five entries use the repository's PyTorch OpenAI Whisper runtime. They
support offline transcription and application-level chunked live use, but not
native streaming. The current application always invokes Whisper with
`task="transcribe"`; translation is a separate Google text call. None provides
diarization. Model files are free to download; infrastructure and electricity
costs remain deployment costs. Audio stays local during ASR.

| Model/version | Functionality | Accuracy classification | Streaming support | Translation support | Diarization | Hardware requirement | Privacy implication | Pricing | Benchmark status | Known limitations |
|---|---|---|---|---|---|---|---|---|---|---|
| `tiny` checkpoint pinned by repository SHA-256 | Multilingual ASR | Unclassified | Chunked application emulation; no native streaming | No in current app | No | CPU supported; GPU optional; project legacy guide uses about 1 GB VRAM | Local ASR audio | No model fee | Not run | Lowest-capacity implemented checkpoint; quality unknown on internal corpus |
| `base` SHA-256 `ed3a0b…6e34e` | Multilingual ASR; current file/live and accurate-final development default | Unclassified | Application semantic partial/stable/final over completed VAD segments; no native token streaming | No in current app | No | CPU supported; GPU optional; project legacy guide uses about 1 GB VRAM | Local ASR audio and local glossary context | No model fee | Automated pipeline tests pass; acoustic benchmark not run | Semantic states and glossary correction cannot replace acoustic validation |
| `small` checkpoint pinned by repository SHA-256 | Multilingual ASR | Unclassified | Chunked application emulation; no native streaming | No in current app | No | CPU supported; GPU optional; project legacy guide uses about 2 GB VRAM | Local ASR audio | No model fee | Not run | Higher compute/memory than base; internal latency unknown |
| `medium` checkpoint pinned by repository SHA-256 | Multilingual ASR | Unclassified | Chunked application emulation; no native streaming | No in current app | No | CPU supported; GPU optional; project legacy guide uses about 5 GB VRAM | Local ASR audio | No model fee | Not run | Internal latency and memory have not been measured |
| `large` alias to `large-v3.pt`, pinned by repository SHA-256 | Multilingual ASR; configurable accurate-final model | Unclassified | Chunked application emulation; no native streaming | No in current app | No | CPU technically supported; GPU practical; project legacy guide uses about 10 GB VRAM | Local ASR audio and local glossary context | No model fee | Not run | Largest current checkpoint; final-path latency and memory are not measured |

Hardware figures above are existing project heuristics from
`src/logic/model_requirements.py`, not measured minimums or guarantees. The
benchmark runner records observed CPU, RAM, GPU, and VRAM so they can be
replaced with evidence.

## Local terminology layer

Stage 6 terminology control is model-independent and may provide local prompt
context plus deterministic whole-word post-correction to the live and
accurate-final paths. Raw model output remains available alongside corrected
text and applied corrections. The glossary does not change any model's
accuracy classification or benchmark status, and is not a substitute for
acoustic accuracy. It is disabled by default.

## Local transcript post-processing layer

| Component/version | Functionality | Accuracy classification | Streaming support | Translation support | Diarization | Hardware requirement | Privacy implication | Pricing | Benchmark status | Known limitations |
|---|---|---|---|---|---|---|---|---|---|---|
| Stage 10 deterministic rules, repository revision | Post-process only final/accurate-final local transcripts with protected formatting, optional filler handling, repetition removal, and paragraph segmentation | Unclassified; syntax/constraint processing, not acoustic correction | Asynchronous completed-segment jobs; no partial processing | No | No | Negligible CPU/RAM; no GPU required | All transcript variants and glossary context remain local | local rule-based code — no per-request fee; infrastructure costs remain | Automated rule, protection, priority, queue, isolation, and fallback tests pass; internal quality review and microphone E2E pending | Cannot repair ASR meaning; conservative number/date rules; sentence-count paragraphs; configured filler ambiguity; process-local state |

No additional model is introduced. See
`docs/stage10-transcript-postprocessing.md` for rule order and safety behavior.

## Local final-translation quality layer

| Component/version | Functionality | Accuracy classification | Streaming support | Translation support | Diarization | Hardware requirement | Privacy implication | Pricing | Benchmark status | Known limitations |
|---|---|---|---|---|---|---|---|---|---|---|
| Stage 8 deterministic rules, repository revision | Post-process only completed local translations: punctuation, capitalization, whitespace, repeated phrases, and terminology | Unclassified; this is formatting/constraint enforcement, not an accuracy model | No; bounded asynchronous final-result job | Language-neutral formatting with configured glossary target forms | No | Negligible CPU/RAM relative to translation inference; no GPU required | Source/final translation and glossary remain local | local rule-based code — no per-request fee; normal infrastructure costs remain | Automated rule, safety, queue, fallback, and integration tests pass; internal human quality review not run | Cannot repair semantic mistranslation; conservative invariant checks cover digits, listed negations, and recognized speaker patterns rather than full semantic equivalence |

## Implemented local diarization model

| Model/version | Functionality | Accuracy classification | Streaming support | Translation support | Diarization | Hardware requirement | Privacy implication | Pricing | Benchmark status | Known limitations |
|---|---|---|---|---|---|---|---|---|---|---|
| SpeechBrain `speechbrain/spkrec-ecapa-voxceleb`, exact downloaded Hugging Face commit recorded | Local speaker embeddings plus session-level online cosine clustering and assignment | Unclassified; upstream VoxCeleb results are not an internal application result | Asynchronous processing of completed VAD segments; not partial-chunk or token streaming | No; assignment is independent from translation | Yes, one dominant speaker assignment per final segment with renameable session-local IDs | CPU supported; CUDA optional; about 90 MB model download plus PyTorch/SpeechBrain runtime and embedding state | Final segment audio and speaker embeddings stay on the host | local model — no per-request fee; infrastructure, storage, electricity, and operations still cost money | Automated lifecycle, isolation, mapping, invariant, and queue tests pass; exact-checkpoint synthetic CPU embedding smoke passes; internal diarization benchmark and microphone E2E pending | Overlapping speech is not separated; online order-dependent clustering; confidence is not calibrated; short/noisy/far-field speech unmeasured; runtime mappings do not survive process restart |

The component is disabled by default and only consumes completed PCM + VAD
segments. See `docs/stage9-local-speaker-diarization.md` for metadata,
configuration, confidence semantics, failure behavior, and validation status.

## Implemented translation providers

| Provider/model version | Functionality | Accuracy classification | Streaming support | Translation support | Diarization | Hardware requirement | Privacy implication | Pricing | Benchmark status | Known limitations |
|---|---|---|---|---|---|---|---|---|---|---|
| Hugging Face Transformers `<5`; Marian `Helsinki-NLP/opus-mt-id-en`, runtime revision/checkpoint recorded | Local Indonesian-to-English text translation from stable/final semantic transcripts | Unclassified; upstream Tatoeba score is not an internal application result | Application preview/final events; no token streaming | `id` → `en` by default; model/language pair configurable | No | CPU supported; CUDA optional; model download/cache and local RAM/VRAM required | Transcript and glossary remain on the host | local model — no per-request fee; infrastructure, storage, electricity, and operations still cost money | Automated state/queue tests and synthetic CPU/glossary smoke pass; internal translation benchmark and microphone E2E not run | Pair-specific checkpoint; 512-token model input; prior segments are a bounded batch context and Marian has no cross-segment attention; glossary marker survival depends on model output; code-switching and domain accuracy unmeasured |
| `deep-translator` GoogleTranslator (remote version not pinned by result) | Text translation after ASR | Unclassified | No | Text translation to provider-supported targets | No | Network access; negligible local inference hardware | Transcript text leaves the local system for Google service processing | Not declared by repository; terms/cost must be verified before production | Not run | Not local-first; remote model/version and data handling are not recorded; no live stable translation |

## Optional OpenAI transcription provider

Stage 17 adds an explicitly selected, server-side cloud provider. It is disabled
by default, API-only (not downloadable), and never receives audio through an
implicit local-to-cloud fallback. Internal accuracy remains **Unclassified**:
the Stage 15 dataset has not been sent to OpenAI because no key, billing
approval, or cloud-dataset consent was supplied. Prices below are separate from
accuracy and were checked against official model pages on 2026-07-30; the
runtime reads `config/openai-pricing.json` because prices may change.

| Model | Functionality | Internal benchmark | Accuracy | Live/final | Streaming | Translation | Diarization | Local/cloud | Availability | Privacy | Hardware | License/service terms | Pricing (checked 2026-07-30) | Known limitations |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `gpt-realtime-whisper` | Low-latency speech-to-text over server-side Realtime WebSocket | Not run; adapter/tests only | Unclassified | Live | Native deltas/completed mapped to partial/stable/final | No | No | Cloud | API-only, not downloadable | Audio leaves deployment after explicit selection/consent | Network plus OpenAI-managed compute; 16 kHz canonical PCM resampled to 24 kHz at boundary | Proprietary OpenAI API service; deployment must review current terms | US$0.017/audio minute | External availability/rate limits; stable depends on provider event availability; estimate may differ from billing |
| `gpt-4o-transcribe` | Accurate-final ASR for complete VAD segments | Not run; adapter/tests only | Unclassified | Final | No live delta stream | No | No | Cloud | API-only, not downloadable | Segment audio and glossary prompt leave deployment | Network plus OpenAI-managed compute | Proprietary OpenAI API service; deployment must review current terms | US$2.50 input / US$10 output per 1M audio tokens | Usage tokens required for meaningful estimate; failure preserves prior transcript |
| `gpt-4o-mini-transcribe` | Optional lower-priced accurate-final ASR | Not run | Unclassified | Final | No live delta stream | No | No | Cloud | API-only, not downloadable | Segment audio and glossary prompt leave deployment | Network plus OpenAI-managed compute | Proprietary OpenAI API service; deployment must review current terms | US$1.25 input / US$5 output per 1M audio tokens | Not default; domain and code-switch accuracy unmeasured internally |
| `gpt-4o-transcribe-diarize` | Optional final ASR with provider speaker diarization | Not run; rich diarized mapping pending | Unclassified | Final | No live delta stream | No | Yes; separate from Stage 9 local mapping | Cloud | API-only, not downloadable | Segment audio leaves deployment | Network plus OpenAI-managed compute | Proprietary OpenAI API service; deployment must review current terms | US$2.50 input / US$10 output per 1M audio tokens | Current lifecycle stores transcript while rich diarized response mapping remains limited |

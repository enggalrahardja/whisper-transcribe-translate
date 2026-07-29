# Model catalogue — implemented and recorded options

Accuracy classification uses **Unclassified** until the internal suite has
enabled, reviewed cases and produces results. No model is labelled “best”.

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

## Local final-translation quality layer

| Component/version | Functionality | Accuracy classification | Streaming support | Translation support | Diarization | Hardware requirement | Privacy implication | Pricing | Benchmark status | Known limitations |
|---|---|---|---|---|---|---|---|---|---|---|
| Stage 8 deterministic rules, repository revision | Post-process only completed local translations: punctuation, capitalization, whitespace, repeated phrases, and terminology | Unclassified; this is formatting/constraint enforcement, not an accuracy model | No; bounded asynchronous final-result job | Language-neutral formatting with configured glossary target forms | No | Negligible CPU/RAM relative to translation inference; no GPU required | Source/final translation and glossary remain local | local rule-based code — no per-request fee; normal infrastructure costs remain | Automated rule, safety, queue, fallback, and integration tests pass; internal human quality review not run | Cannot repair semantic mistranslation; conservative invariant checks cover digits, listed negations, and recognized speaker patterns rather than full semantic equivalence |

## Implemented translation providers

| Provider/model version | Functionality | Accuracy classification | Streaming support | Translation support | Diarization | Hardware requirement | Privacy implication | Pricing | Benchmark status | Known limitations |
|---|---|---|---|---|---|---|---|---|---|---|
| Hugging Face Transformers `<5`; Marian `Helsinki-NLP/opus-mt-id-en`, runtime revision/checkpoint recorded | Local Indonesian-to-English text translation from stable/final semantic transcripts | Unclassified; upstream Tatoeba score is not an internal application result | Application preview/final events; no token streaming | `id` → `en` by default; model/language pair configurable | No | CPU supported; CUDA optional; model download/cache and local RAM/VRAM required | Transcript and glossary remain on the host | local model — no per-request fee; infrastructure, storage, electricity, and operations still cost money | Automated state/queue tests and synthetic CPU/glossary smoke pass; internal translation benchmark and microphone E2E not run | Pair-specific checkpoint; 512-token model input; prior segments are a bounded batch context and Marian has no cross-segment attention; glossary marker survival depends on model output; code-switching and domain accuracy unmeasured |
| `deep-translator` GoogleTranslator (remote version not pinned by result) | Text translation after ASR | Unclassified | No | Text translation to provider-supported targets | No | Network access; negligible local inference hardware | Transcript text leaves the local system for Google service processing | Not declared by repository; terms/cost must be verified before production | Not run | Not local-first; remote model/version and data handling are not recorded; no live stable translation |

## OpenAI provider options (documentation only; not integrated)

OpenAI is recorded as a possible future cloud provider. Stage 1 contains no API
client, key setting, runtime branch, or provider implementation. Pricing and
capabilities are intentionally marked for verification at the integration
stage, because they may change.

| Model | Functionality | Accuracy classification | Streaming support | Translation support | Diarization | Hardware requirement | Privacy implication | Pricing | Benchmark status | Known limitations |
|---|---|---|---|---|---|---|---|---|---|---|
| `gpt-4o-transcribe` | Cloud speech-to-text option | Unclassified | Verify before integration | Not integrated | No in current app | Network connection; provider-hosted compute | Audio would leave local environment when explicitly selected | Paid; verify current official pricing before integration | Not integrated / not run | Cloud dependency, usage cost, data-governance review required |
| `gpt-4o-mini-transcribe` | Lower-cost cloud speech-to-text option | Unclassified | Verify before integration | Not integrated | No in current app | Network connection; provider-hosted compute | Audio would leave local environment when explicitly selected | Paid; verify current official pricing before integration | Not integrated / not run | Cloud dependency, usage cost, data-governance review required |
| `gpt-4o-transcribe-diarize` | Cloud transcription with speaker attribution option | Unclassified | Verify before integration | Not integrated | Intended capability; verify before integration | Network connection; provider-hosted compute | Audio would leave local environment when explicitly selected | Paid; verify current official pricing before integration | Not integrated / not run | Not a current provider; exact behavior and constraints must be validated |

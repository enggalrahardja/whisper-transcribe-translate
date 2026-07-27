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
| `base` SHA-256 `ed3a0b…6e34e` | Multilingual ASR; current file/live default | Unclassified | Chunked application emulation; no stable event | No in current app | No | CPU supported; GPU optional; project legacy guide uses about 1 GB VRAM | Local ASR audio | No model fee | Not run; checkpoint present on audit machine | Live output arrives per complete chunk; final copies partial |
| `small` checkpoint pinned by repository SHA-256 | Multilingual ASR | Unclassified | Chunked application emulation; no native streaming | No in current app | No | CPU supported; GPU optional; project legacy guide uses about 2 GB VRAM | Local ASR audio | No model fee | Not run | Higher compute/memory than base; internal latency unknown |
| `medium` checkpoint pinned by repository SHA-256 | Multilingual ASR | Unclassified | Chunked application emulation; no native streaming | No in current app | No | CPU supported; GPU optional; project legacy guide uses about 5 GB VRAM | Local ASR audio | No model fee | Not run | Internal latency and memory have not been measured |
| `large` alias to `large-v3.pt`, pinned by repository SHA-256 | Multilingual ASR | Unclassified | Chunked application emulation; no native streaming | No in current app | No | CPU technically supported; GPU practical; project legacy guide uses about 10 GB VRAM | Local ASR audio | No model fee | Not run | Largest current checkpoint; no separate live/final role and no measured requirement |

Hardware figures above are existing project heuristics from
`src/logic/model_requirements.py`, not measured minimums or guarantees. The
benchmark runner records observed CPU, RAM, GPU, and VRAM so they can be
replaced with evidence.

## Implemented translation provider

| Provider/model version | Functionality | Accuracy classification | Streaming support | Translation support | Diarization | Hardware requirement | Privacy implication | Pricing | Benchmark status | Known limitations |
|---|---|---|---|---|---|---|---|---|---|---|
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

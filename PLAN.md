# Implementation Plan — Whisper Transcribe & Translate

## 1. Project Direction

The project will use a **local-first architecture** during initial development.

Primary development requirements:

- Core transcription and translation must work without a paid cloud API.
- Local models are the default providers.
- OpenAI models are optional providers and must be selectable through configuration.
- Live results must prioritize low latency.
- Final results must prioritize accuracy.
- Every supported model must be documented with its accuracy profile, functionality, deployment method, hardware needs, and pricing when applicable.
- Model-provider logic must remain separate from session, UI, storage, and processing workflow.

## 2. Target Architecture

```text
Microphone / Audio File
        │
        ▼
Audio Capture & Preprocessing
VAD · noise suppression · echo cancellation · gain control
        │
        ├── Fast Live Path
        │   ├── Streaming / chunked local ASR
        │   ├── Partial transcript
        │   ├── Stable transcript
        │   ├── Live translation
        │   └── WebSocket delivery to UI
        │
        └── Accurate Final Path
            ├── Final high-accuracy ASR
            ├── Timestamp alignment
            ├── Speaker diarization
            ├── Terminology correction
            ├── Final translation
            └── Replace provisional result
```

## 3. Model Strategy

### 3.1 Development Default: Local Models

| Function | Initial model | Accuracy profile | Performance profile | Pricing |
|---|---|---|---|---|
| Live transcription | Whisper `large-v3-turbo` through `faster-whisper` | High, but normally below full `large-v3` for difficult audio | Fastest recommended Whisper variant for live development | Model free; infrastructure cost only |
| Final transcription | Whisper `large-v3` through `faster-whisper` | Highest accuracy among the selected downloadable Whisper variants | Slower and requires more VRAM | Model free; infrastructure cost only |
| Translation | Local text translation provider, initially configurable | Must be benchmarked for Indonesian ↔ English and mixed-language speech | Runs after stable or final source transcript | Model free; infrastructure cost only |
| VAD | Silero VAD or equivalent local VAD | High speech/non-speech reliability after threshold tuning | Lightweight and suitable for realtime use | Free/open model |
| Diarization | Local diarization provider | Accuracy depends heavily on audio quality and overlapping speech | Run asynchronously so it does not block live transcription | Model free; infrastructure cost only |

Local model licensing and permitted use must be verified before production distribution.

### 3.2 Optional OpenAI Providers

| Model | Functionality | Accuracy / positioning | Deployment | Published pricing* |
|---|---|---|---|---:|
| `gpt-4o-transcribe` | Accurate speech-to-text for completed audio or finalized segments | OpenAI positions it as more accurate than original Whisper models, with improved WER and language recognition | OpenAI API only; not downloadable | Audio input US$2.50 / 1M tokens; output US$10 / 1M tokens |
| `gpt-4o-mini-transcribe` | Lower-cost speech-to-text option | Faster/lower-cost alternative; benchmark against local Turbo | OpenAI API only | Audio input starts at US$1.25 / 1M tokens; verify current output pricing before release |
| `GPT-Realtime-Whisper` | Streaming low-latency speech-to-text | Designed specifically for live transcription while the speaker is talking | OpenAI Realtime API only | US$0.017 per audio minute |
| `GPT-Realtime-Translate` | Live speech translation | Supports more than 70 input languages and 13 output languages according to OpenAI | OpenAI Realtime API only | US$0.034 per audio minute |
| `gpt-4o-transcribe-diarize` | Transcription with speaker labels | Optional cloud diarization path | OpenAI API only | Verify current official pricing before enabling in production |

\* Pricing is informational and must be rechecked from official pricing documentation before each production release.

### 3.3 Required Model Presentation in the Application

Every selectable model must display:

- Provider.
- Model name and version.
- Local or cloud deployment.
- Intended function.
- Accuracy classification: Fast, Balanced, Accurate, or Experimental.
- Supported languages.
- Live streaming support.
- Translation support.
- Speaker diarization support.
- Required CPU, RAM, GPU, and VRAM.
- Expected latency class.
- Privacy implication.
- Pricing and pricing unit when paid.
- Benchmark status on the project's own test dataset.
- Known limitations.

No model may be labelled “best” solely from vendor claims. Final classification must be based on project benchmarks.

## 4. Processing Profiles

### Local Fast

```text
Live ASR       : Whisper large-v3-turbo
Final ASR      : Whisper large-v3-turbo
Translation    : Local translation model
Diarization    : Optional asynchronous local model
Cloud cost     : None
```

Purpose: development, low-resource deployment, and lowest finalization delay.

### Local Balanced — Initial Default

```text
Live ASR       : Whisper large-v3-turbo
Final ASR      : Whisper large-v3
Translation    : Local translation model
Diarization    : Local asynchronous model
Cloud cost     : None
```

Purpose: primary development and private/on-premise deployment.

### OpenAI Accurate

```text
Live ASR       : GPT-Realtime-Whisper
Final ASR      : gpt-4o-transcribe
Live translate : GPT-Realtime-Translate or text translation pipeline
Final translate: Context-aware cloud translation
Diarization    : Optional gpt-4o-transcribe-diarize
Cloud cost     : Usage-based
```

Purpose: production comparison and optional premium accuracy/latency profile.

### Hybrid

```text
Live ASR       : Local Whisper large-v3-turbo
Final ASR      : gpt-4o-transcribe
Translation    : Local live + cloud final
Diarization    : Local or cloud selectable
```

Purpose: keep live operation available locally while selectively using cloud refinement.

## 5. Implementation Stages

### Stage 1 — Baseline, Dataset, and Acceptance Metrics

- Audit the existing repository and processing flow.
- Prepare representative audio datasets:
  - Indonesian.
  - English.
  - Indonesian–English code-switching.
  - Technical meetings.
  - Quiet microphone audio.
  - Far-field meeting-room audio.
  - Background noise.
  - Multiple speakers.
  - Overlapping speech.
- Create verified reference transcripts and translations.
- Measure:
  - Word Error Rate or Character Error Rate.
  - Translation quality using human review and automatic metrics where useful.
  - Partial-result latency.
  - Stable-result latency.
  - Final-result latency.
  - Real-time factor.
  - CPU, RAM, GPU, and VRAM usage.

Deliverable: reproducible benchmark suite and baseline report.

### Stage 2 — Provider Abstraction

Create contracts without coupling the application to one vendor:

```text
TranscriptionProvider
StreamingTranscriptionProvider
TranslationProvider
DiarizationProvider
VoiceActivityProvider
```

Each provider must expose:

- Capability metadata.
- Model metadata.
- Health status.
- Configuration validation.
- Processing metrics.
- Standardized errors.

Deliverable: local and OpenAI providers can be selected without changing UI workflow or storage schema.

### Stage 3 — Local Model Runtime

- Integrate `faster-whisper`.
- Load models once in persistent workers.
- Support CPU and CUDA execution.
- Support configurable compute types such as FP16 or INT8 where valid.
- Implement model download/cache management.
- Add startup readiness and health checks.
- Prevent repeated model loading per request.
- Add bounded queue, timeout, cancellation, and graceful shutdown.

Deliverable: reliable local inference service.

### Stage 4 — Audio Capture and Ingestion

```text
Microphone
→ AudioWorklet
→ normalized mono PCM
→ short chunks
→ WebSocket gateway
→ per-session audio buffer
```

Implement:

- Microphone selection.
- Audio level monitoring.
- Noise suppression.
- Echo cancellation.
- Automatic gain control.
- Connection recovery.
- Sequence numbers.
- Lost-chunk detection.
- Session isolation.
- Original audio recording when enabled.

Deliverable: stable realtime audio transport with no silent data loss.

### Stage 5 — Voice Activity Detection and Segmentation

- Add local VAD.
- Preserve short pre-speech buffer.
- Detect speech start and endpoint.
- Configure silence threshold.
- Enforce maximum segment duration.
- Add overlap/context between finalized segments.
- Reject empty or near-silent segments.

Initial tuning range:

```text
Audio chunk          : 100–250 ms
Pre-speech buffer    : 200–300 ms
Silence endpoint     : 400–700 ms
Context overlap      : 1–2 seconds
Maximum segment      : 15–30 seconds
```

Deliverable: natural segments without clipped words.

### Stage 6 — Local Live Transcription

- Use Whisper `large-v3-turbo` as the initial live model.
- Implement partial, stable, and final segment states.
- Allow partial text to be revised.
- Prevent duplicate append behavior.
- Preserve sequence and timestamps.
- Use language hints when configured.
- Include previous stable context where useful.

Deliverable: responsive live transcription using only local models.

### Stage 7 — Accurate Final Transcription

- Reprocess finalized segments using Whisper `large-v3`.
- Replace provisional text only when revision ordering is valid.
- Apply punctuation and capitalization.
- Detect repetitions and hallucinated silence text.
- Preserve numbers, dates, identifiers, and technical terms.
- Store raw, live, and final transcript revisions.

Deliverable: high-accuracy final transcript without blocking the live path.

### Stage 8 — Glossary and Context Management

Glossary fields:

```text
Term
Preferred spelling
Source language
Preferred translation
Do-not-translate
Aliases
Category
Priority
Active status
```

Use glossary in:

- ASR prompts or hotword context where supported.
- Post-transcription correction.
- Translation.
- Subtitle and document export.

Deliverable: consistent internal and technical terminology.

### Stage 9 — Local Translation

- Translate stable transcript segments, not unstable tokens.
- Preserve previous segment context.
- Enforce glossary rules.
- Keep source and target revisions linked.
- Detect missing numbers, dates, names, and clauses.
- Re-run final translation when final transcript changes.

Deliverable: fully local translation pipeline.

### Stage 10 — OpenAI Optional Providers

- Add OpenAI API configuration as an optional feature.
- Keep API keys server-side and encrypted at rest.
- Support:
  - `gpt-4o-transcribe` for final transcription.
  - `GPT-Realtime-Whisper` for cloud live transcription.
  - `GPT-Realtime-Translate` for cloud live translation.
  - Diarization-capable transcription when selected.
- Display current pricing reference and cost estimator.
- Track usage per session and model.
- Add hard budget and usage limits.
- Fail safely to configured local providers when allowed.

Deliverable: selectable cloud capability without making cloud access mandatory.

### Stage 11 — Speaker Diarization

- Run diarization asynchronously.
- Assign speaker labels to timestamped transcript segments.
- Support speaker rename and merge.
- Handle uncertain and overlapping speech states.
- Do not block live transcription while diarization is pending.

Deliverable: multi-speaker transcript with editable speaker identities.

### Stage 12 — User Interface

Main session UI:

- Input device.
- Source and target language.
- Processing profile.
- Explicit model/provider selection.
- Model capability and pricing details.
- Audio status.
- Live source transcript.
- Live translation.
- Partial/stable/final visual states.
- Speaker labels.
- Pause, resume, stop, retry, and export.

Model selection UI must never hide whether audio leaves the local system.

Deliverable: transparent and controllable transcription experience.

### Stage 13 — Persistence and Revision Model

Minimum entities:

```text
TranscriptionSession
AudioSegment
TranscriptRevision
TranslationRevision
Speaker
GlossaryTerm
ModelDefinition
ModelConfiguration
ProcessingJob
UsageRecord
QualityMetric
```

Store per result:

- Provider and exact model version.
- Start/end timestamps.
- Processing state.
- Confidence when available.
- Latency.
- Cost estimate when applicable.
- Revision number.
- Error and retry history.

Deliverable: traceable and reproducible results.

### Stage 14 — Monitoring and Quality Dashboard

Measure:

- Partial latency.
- Stable latency.
- Final latency.
- Translation latency.
- Real-time factor.
- Audio drop rate.
- Empty segment rate.
- Duplicate text rate.
- Queue depth.
- Worker health.
- GPU utilization.
- API usage and estimated cost.
- Session failure rate.

User quality feedback:

```text
Correct
Missing words
Wrong words
Wrong language
Wrong translation
Wrong speaker
Bad timestamp
```

Deliverable: measurable model and production quality.

### Stage 15 — Comparative Benchmark and Model Catalogue

For every model/profile, publish:

- Dataset version.
- Hardware used.
- Accuracy score.
- Translation score.
- Live latency percentiles.
- Final latency percentiles.
- Resource consumption.
- Pricing estimate.
- Privacy classification.
- Recommended use case.

Deliverable: clear model decision matrix based on evidence.

### Stage 16 — Production Hardening

- Authentication and authorization.
- API secret management.
- Encryption in transit and at rest.
- Audio retention policy.
- User consent and recording indicator.
- Audit trail.
- Rate limits.
- Session duration and concurrency limits.
- Worker recovery.
- Backpressure.
- Load tests.
- Security tests.
- Configurable local-only mode.

Deliverable: production-ready deployment.

## 6. Acceptance Targets

Initial engineering targets:

| Metric | Target |
|---|---:|
| First partial transcript | Under 1 second on supported hardware |
| Stable live transcript | Under 2 seconds |
| Live translation after stable text | Under 1 additional second |
| Final local segment result | Under 5 seconds after endpoint on supported GPU |
| Lost accepted audio chunks | 0 |
| Duplicate finalized segments | 0 |
| Cloud dependency during local profile | 0 |
| Model/provider shown to user | 100% |
| Paid usage recorded | 100% |
| Technical glossary consistency | Measured and included in benchmark |

Hardware-specific latency must be validated and documented; it must not be guaranteed before benchmark results exist.

## 7. Initial Development Priority

```text
1. Benchmark dataset and metrics
2. Provider contracts
3. Local faster-whisper runtime
4. Audio ingestion
5. VAD and segmentation
6. Local live transcription
7. Local final transcription
8. Glossary
9. Local translation
10. UI model transparency
11. OpenAI optional providers
12. Diarization
13. Monitoring and benchmark catalogue
14. Production hardening
```

## 8. Official OpenAI References

- GPT-4o Transcribe model documentation: https://developers.openai.com/api/docs/models/gpt-4o-transcribe
- OpenAI audio model announcement: https://openai.com/index/introducing-our-next-generation-audio-models/
- OpenAI realtime voice models and published pricing: https://openai.com/index/advancing-voice-intelligence-with-new-models-in-the-api/
- OpenAI Realtime API documentation: https://platform.openai.com/docs/api-reference/realtime


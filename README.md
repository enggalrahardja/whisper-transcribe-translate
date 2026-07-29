# Whisper Transcribe & Translate

A local-first speech transcription and translation platform designed for:

- High-accuracy transcription.
- Fast live transcription.
- Live and final translation.
- Indonesian, English, and mixed-language conversations.
- Technical terminology and custom glossary support.
- Optional speaker diarization.
- Transparent selection between local and paid cloud models.

## Project Principles

1. **Local models are the development default.**
2. The core application must remain usable without a paid API.
3. OpenAI models are optional providers, not hard dependencies.
4. Live processing and final processing use separate quality/latency paths.
5. The active model, functionality, expected accuracy, privacy impact, and pricing must be shown clearly.
6. Model quality claims must be validated against the project's own benchmark dataset.
7. Audio must never be sent to a cloud provider unless a cloud profile is explicitly configured and selected.

## Processing Concept

```text
Audio input
   │
   ▼
Preprocessing and VAD
   │
   ├── Live path
   │   ├── Fast ASR
   │   ├── Partial/stable transcript
   │   └── Live translation
   │
   └── Final path
       ├── Accurate ASR
       ├── Terminology correction
       ├── Speaker diarization
       └── Final translation
```

The live path provides responsive captions. The final path revises each completed segment using a more accurate model.

## Initial Model Configuration

### Default Local Profile

| Function | Model / runtime | Purpose | Cost |
|---|---|---|---:|
| Live transcription | Whisper `large-v3-turbo` via `faster-whisper` | Low-latency partial and stable transcript | No model usage fee |
| Final transcription | Whisper `large-v3` via `faster-whisper` | Higher-accuracy finalized transcript | No model usage fee |
| Translation | Configurable local translation model | Live and final translation | No model usage fee |
| Voice activity detection | Local VAD | Speech segmentation | No model usage fee |
| Speaker diarization | Configurable local diarization model | Speaker labels | No model usage fee |

Local deployment still incurs hardware, electricity, hosting, and maintenance costs.

### Optional OpenAI Profile

| Model | Main use | Published positioning | Published pricing* |
|---|---|---|---:|
| `gpt-4o-transcribe` | Accurate final transcription | Improved WER and language recognition compared with original Whisper models | US$2.50 / 1M audio input tokens; US$10 / 1M output tokens |
| `GPT-Realtime-Whisper` | Cloud live transcription | Streaming low-latency speech-to-text | US$0.017 per minute |
| `GPT-Realtime-Translate` | Cloud live translation | More than 70 input languages and 13 output languages | US$0.034 per minute |
| `gpt-4o-transcribe-diarize` | Cloud transcription with speaker labels | Transcription plus diarization | Check current official pricing |

\* Prices can change. The application documentation and configuration screen must reference the current official pricing before production activation.

OpenAI models are API services and cannot be downloaded for local execution.

## Processing Profiles

### Local Fast

- Live: Whisper `large-v3-turbo`.
- Final: Whisper `large-v3-turbo`.
- Translation: local.
- Best for lower-resource hardware and faster finalization.

### Local Balanced

- Live: Whisper `large-v3-turbo`.
- Final: Whisper `large-v3`.
- Translation: local.
- Initial development default.

### Hybrid

- Live: local Whisper.
- Final: optional `gpt-4o-transcribe`.
- Translation: local live translation and optional cloud final translation.
- Best when local availability is required but cloud refinement is allowed.

### OpenAI Accurate

- Live: `GPT-Realtime-Whisper`.
- Final: `gpt-4o-transcribe`.
- Translation: `GPT-Realtime-Translate` or configured cloud text translation.
- Usage-based cost.

## Model Transparency Requirements

The model catalogue and model selector must show:

- Provider.
- Model and version.
- Local or cloud execution.
- Supported functionality.
- Accuracy category.
- Supported languages.
- Streaming support.
- Translation support.
- Diarization support.
- Expected latency category.
- CPU/GPU/RAM/VRAM requirements.
- Privacy implications.
- Usage price and pricing unit.
- Internal benchmark results.
- Known limitations.

## Core Functional Scope

- Microphone and audio-file transcription.
- Live partial, stable, and final transcript states.
- Live and final translation.
- Automatic language detection and manual language selection.
- Indonesian–English code-switching.
- Custom technical glossary.
- Segment timestamps.
- Speaker diarization and speaker rename.
- Transcript revision history.
- Audio/session persistence based on retention policy.
- Subtitle and transcript export.
- Local/cloud processing profiles.
- Usage and cost tracking for paid providers.
- Quality and latency monitoring.

## Target Components

```text
Frontend
├── Audio capture
├── WebSocket client
├── Live transcript renderer
├── Translation renderer
├── Model/profile selector
└── Session controls

Backend Gateway
├── Session manager
├── WebSocket ingestion
├── Audio buffer
├── Provider selection
└── Result broadcaster

Processing Workers
├── VAD worker
├── Live ASR worker
├── Final ASR worker
├── Translation worker
├── Diarization worker
└── Export worker

Persistence
├── Sessions
├── Audio segments
├── Transcript revisions
├── Translation revisions
├── Speakers
├── Glossary
├── Usage records
└── Quality metrics
```

## Expected Result States

```text
partial   → provisional text that may change
stable    → live text considered stable for translation
final     → finalized high-accuracy text
corrected → optional reviewed or terminology-corrected text
```

Partial text must never be blindly appended as permanent text. Final revisions replace the corresponding provisional segment using sequence and revision control.

## Initial Acceptance Targets

| Metric | Initial target |
|---|---:|
| First partial transcript | Under 1 second on supported hardware |
| Stable transcript | Under 2 seconds |
| Live translation delay | Under 1 second after stable transcript |
| Final local result | Under 5 seconds after segment endpoint on supported GPU |
| Lost accepted audio chunks | 0 |
| Duplicate final segments | 0 |
| Cloud calls in local profile | 0 |
| Paid usage tracked | 100% |

These are engineering targets. Actual guarantees require benchmark results on the selected deployment hardware.

## Development Sequence

1. Create benchmark dataset and reference transcripts.
2. Define provider interfaces.
3. Integrate local `faster-whisper` runtime.
4. Implement audio capture and WebSocket ingestion.
5. Implement VAD and segmentation.
6. Implement local live transcription.
7. Implement local accurate final transcription.
8. Implement glossary and context correction.
9. Implement local translation.
10. Implement model catalogue and transparent selector.
11. Add optional OpenAI providers.
12. Add diarization.
13. Add monitoring, usage, and benchmark reports.
14. Complete production hardening.

Full staged implementation details are available in [PLAN.md](./PLAN.md).

## Configuration Direction

Provider selection should be configuration-based rather than hard-coded.

Example conceptual configuration:

```yaml
processing_profile: local-balanced

transcription:
  live_provider: local
  live_model: whisper-large-v3-turbo
  final_provider: local
  final_model: whisper-large-v3

translation:
  provider: local
  source_language: auto
  target_language: en

openai:
  enabled: false
```

The actual environment variable names and schema will be defined only after the existing repository structure is audited.

## Security and Privacy

- API keys must remain server-side.
- Secrets must be encrypted at rest.
- Cloud processing must be visibly identified.
- Local-only mode must prevent all cloud calls.
- Audio recording must include consent and retention controls.
- Access to audio and transcripts must be authorized and audited.
- Paid-provider usage must support budget and hard-limit controls.

## Benchmark Policy

Every candidate model must be tested using the same:

- Audio dataset.
- Reference transcript.
- Language mix.
- Hardware specification.
- Runtime configuration.
- Accuracy metrics.
- Latency metrics.
- Resource metrics.

Vendor claims may be included as references but must not replace internal benchmark results.

## Official OpenAI References

- GPT-4o Transcribe: https://developers.openai.com/api/docs/models/gpt-4o-transcribe
- OpenAI next-generation audio models: https://openai.com/index/introducing-our-next-generation-audio-models/
- Realtime transcription, translation, and pricing: https://openai.com/index/advancing-voice-intelligence-with-new-models-in-the-api/
- Realtime API: https://platform.openai.com/docs/api-reference/realtime


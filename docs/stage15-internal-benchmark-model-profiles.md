# Stage 15 — internal benchmark and model profiles

## Outcome

The Stage 1 runner now has a versioned, non-sensitive synthetic dataset with
seven enabled files covering Indonesian, English, Indonesian–English
code-switching, technical speech, quiet input, an attenuated far-field proxy,
background noise, multiple speakers, and overlapping speech. Audio is PCM16
mono 16 kHz. References were checked against the exact synthesis scripts; no
production or customer material is present.

This is an actual reproducibility baseline, but not a natural-microphone
accuracy corpus. A later consented human corpus must use a separate version.

## Measured baseline

Run date: 2026-07-29. Dataset: `stage15-safe-synthetic-benchmark` v1.0.0.

| Component/model | Result | Internal accuracy | Latency/resource status |
|---|---|---|---|
| Whisper `base` / `base.pt` | 7/7 completed | mean WER 0.5278; mean CER 0.1852 | mean final 7165.36 ms; RTF 1.7553; load 783.55 ms; mean CPU peak 340.59%; RAM peak 733.15 MiB |
| Whisper `small` | Checkpoint absent | Unclassified | Not run |
| Whisper `medium` | Checkpoint absent | Unclassified | Not run |
| Whisper `large-v3` | Checkpoint absent | Unclassified | Not run |
| Whisper `large-v3-turbo` | Checkpoint/runtime mapping absent | Unclassified | Not run |
| Marian `Helsinki-NLP/opus-mt-id-en` | No pinned local checkpoint | Unclassified | Translation benchmark not run |
| SpeechBrain ECAPA-TDNN | No pinned local checkpoint | Unclassified | Diarization benchmark not run |

No model is called “best”. Partial/stable latency is unavailable from the
offline adapter. GPU/VRAM remain null when NVIDIA telemetry is inaccessible.

## Complete catalogue view

| Model | Functionality | Internal result | Languages | Streaming | Translation | Diarization | Hardware | License | Privacy | Pricing | Date/dataset | Limitations |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Whisper `base` | Local ASR | WER 0.5278, CER 0.1852 | Multilingual | App chunks; no native token stream | No | No | CPU; CUDA optional | MIT code; upstream checkpoint terms | Local audio | No request fee; compute remains | 2026-07-29/v1.0.0 | Synthetic CPU run, beam 5 |
| Whisper `small` | Local ASR | Not measured | Multilingual | App chunks | No | No | CPU/GPU | MIT/upstream checkpoint | Local | No request fee; compute remains | Not run/v1.0.0 | Checkpoint absent |
| Whisper `medium` | Local ASR | Not measured | Multilingual | App chunks | No | No | High CPU/RAM; GPU practical | MIT/upstream checkpoint | Local | No request fee; compute remains | Not run/v1.0.0 | Checkpoint absent |
| Whisper `large-v3` | Local ASR | Not measured | Multilingual | App chunks | No | No | GPU practical | MIT/upstream checkpoint | Local | No request fee; compute remains | Not run/v1.0.0 | Checkpoint absent |
| Whisper `large-v3-turbo` | Local ASR candidate | Not measured | Multilingual | App chunks | No | No | GPU recommended | MIT/upstream checkpoint | Local | No request fee; compute remains | Not run/v1.0.0 | Not in app registry; absent |
| Marian id→en | Local text translation | Not measured | Indonesian→English | Segment preview/final | Yes | No | CPU/CUDA | Apache-2.0 model-card declaration; verify pin | Local transcript | No request fee; compute remains | Not run/v1.0.0 | Pair-specific; checkpoint absent |
| SpeechBrain ECAPA | Embedding/clustering | Not measured | Language-independent intent | Final segments | No | Yes | CPU/CUDA | Apache-2.0 toolkit; model terms apply | Local audio/embedding | No request fee; compute remains | Not run/v1.0.0 | No overlap separation; checkpoint absent |

## Profiles and fallback

`config/model-profiles.json` defines Fast, Balanced, Accurate, and Private.
The resolver validates local providers, exact checkpoints, device, beam,
language, feature snapshot, benchmark reference, and fallback chain.

- **Fast** is the safe default: `base`, beam 1, new pipeline flags off.
- **Balanced** requests `small` plus `medium` accurate-final, falling back to Fast.
- **Accurate** uses the only model that passed this run (`base`) with beam 5
  for accurate-final; it may change only after newer comparative evidence.
- **Private** prohibits network providers; all optional features remain off.

Resolution never downloads models. Missing checkpoints and unsupported CUDA
cause explicit fallback. Legacy behavior is unchanged because profiles are not
automatically applied.

## Reproduction

```powershell
services/api/.venv/Scripts/python.exe benchmarks/run.py validate
services/api/.venv/Scripts/python.exe benchmarks/run.py run --provider local-whisper --model base --model-version "sha256:ed3a0b6b1c0edf879ad9b11b1af5a0e6ab5db9205f891f668f8b0e6c6326e34e" --deployment local --beam-size 5 --provider-command "services/api/.venv/Scripts/python.exe benchmarks/providers/current_whisper.py" --output-dir benchmarks/results/stage15/base
services/api/.venv/Scripts/python.exe benchmarks/stage15.py --output-dir benchmarks/results/stage15
services/api/.venv/Scripts/python.exe -m unittest discover -s benchmarks/tests -v
services/api/.venv/Scripts/python.exe -m unittest discover -s services/api/tests -p test_model_profiles.py -v
```

Repeat for another model only after its exact local checkpoint is available.
The collector emits JSON, CSV, Markdown, and an unsupported report. Failed or
missing results are never converted to zero-valued accuracy.

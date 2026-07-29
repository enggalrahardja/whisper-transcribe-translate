# Stage 15 internal benchmark

Dataset: `stage15-safe-synthetic-benchmark` v`1.0.0`; date: 2026-07-29.

| Component | Model | Available | Status | WER | CER | Final ms | RTF | Load ms | Failure rate |
|---|---|---:|---|---:|---:|---:|---:|---:|---:|
| transcription | base | True | completed | 0.5278 | 0.1852 | 7165.3573 | 1.7553 | 783.5550 | 0.0000 |
| transcription | small | False | not_run | n/a | n/a | n/a | n/a | n/a | n/a |
| transcription | medium | False | not_run | n/a | n/a | n/a | n/a | n/a | n/a |
| transcription | large-v3 | False | not_run | n/a | n/a | n/a | n/a | n/a | n/a |
| transcription | large-v3-turbo | False | not_run | n/a | n/a | n/a | n/a | n/a | n/a |
| translation | Helsinki-NLP/opus-mt-id-en | False | not_run | n/a | n/a | n/a | n/a | n/a | n/a |
| diarization | speechbrain/spkrec-ecapa-voxceleb | False | not_run | n/a | n/a | n/a | n/a | n/a | n/a |

## Unsupported/unexecuted

- `small` (transcription): checkpoint missing: storage/models/whisper/small.pt
- `medium` (transcription): checkpoint missing: storage/models/whisper/medium.pt
- `large-v3` (transcription): checkpoint missing: storage/models/whisper/large-v3.pt
- `large-v3-turbo` (transcription): checkpoint missing: storage/models/whisper/large-v3-turbo.pt
- `Helsinki-NLP/opus-mt-id-en` (translation): Marian checkpoint/dependency not available locally
- `speechbrain/spkrec-ecapa-voxceleb` (diarization): ECAPA checkpoint not pinned in local benchmark cache

## Interpretation

No model is designated as best. Only successful rows are measured; unavailable rows are explicit. Profiles fall back to Fast/base and remain disabled by default.

## Limitations

- Synthetic speech is a reproducibility fixture and does not represent natural microphone accuracy.
- Offline Whisper emits final only; partial and stable latency are not applicable.
- Model comparison is incomplete until all checkpoints run on this exact dataset and hardware.

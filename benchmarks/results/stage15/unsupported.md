# Unsupported hardware/model report

## Hardware

- NVIDIA GPU telemetry unavailable: NVIDIA-SMI has failed because you do not have suffient permissions. Please try running as an administrator. CPU fallback is required.

## Models and components

- **small**: checkpoint missing: storage/models/whisper/small.pt
- **medium**: checkpoint missing: storage/models/whisper/medium.pt
- **large-v3**: checkpoint missing: storage/models/whisper/large-v3.pt
- **large-v3-turbo**: checkpoint missing: storage/models/whisper/large-v3-turbo.pt
- **Helsinki-NLP/opus-mt-id-en**: Marian checkpoint/dependency not available locally
- **speechbrain/spkrec-ecapa-voxceleb**: ECAPA checkpoint not pinned in local benchmark cache

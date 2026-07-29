# Stage 18 known limitations

- Physical browser microphone E2E was not executed in this headless environment unless explicit operator evidence says otherwise.
- API process restart against a real MongoDB and storage deployment was not executed; repository-level restore is automated.
- Translation and SpeechBrain diarization checkpoints were unavailable in the Stage 15 hardware snapshot, so their real-model E2E quality/latency remains unmeasured.
- Whisper base partial/stable latency is unavailable because the existing model is segment-based rather than true token streaming.
- GPU/VRAM values remain unavailable when NVIDIA telemetry or supported hardware is absent.
- OpenAI was not called without all credential, billing, and dataset-consent gates.
- In-process queues and provider rate limits remain non-durable and per-process.

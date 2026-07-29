# Stage 18 — end-to-end acceptance and release readiness

## Verdict

**NO-GO for production release.** All automated checks and the current local
Whisper `base` benchmark completed, but the evidence does not satisfy the
mandatory production gates. Physical microphone E2E, API restart/restore
against deployed MongoDB/storage, deployed TLS/origin/secret-management checks,
and real-model Marian/SpeechBrain E2E were not available. This is deliberately
not described as production-ready.

Machine-readable evidence and generated reports are in
`acceptance/stage18/results/`:

- `acceptance.json`
- `acceptance-report.md`
- `pass-fail-matrix.md`
- `performance-report.md`
- `security-checklist.md`
- `known-limitations.md`
- `local-base/results.json`, `results.csv`, and `report.md`

## Current evidence

The acceptance run used seven reviewed, non-sensitive synthetic fixtures on the
same hardware and dataset version. Indonesian, English, code-switching,
technical, quiet, noisy, far-field, multi-speaker ASR, and overlapping-speech
ASR cases completed. The automated API suite covers PCM sequence anomalies,
VAD, semantic revisions, accurate-final replacement, glossary, translation and
quality lifecycles, diarization clustering/assignment, transcript processing,
persistence degradation/restore, monitoring redaction, worker isolation,
security, provider consent, and release profiles.

This distinction matters: passing queue/rule tests is not evidence that an
unavailable Marian or SpeechBrain checkpoint produces acceptable real audio
results. Likewise, synthetic audio ingestion does not replace a physical
AudioWorklet/microphone test.

Current `base` result:

| Dataset/model | Accuracy | Latency/RTF | Resources | Positioning |
|---|---|---|---|---|
| `stage15-safe-synthetic-benchmark` v1.0.0, local Whisper `base.pt`, CPU float32, beam 5 | mean WER 0.5278; mean CER 0.1852; 7/7 completed | mean final 5649.03 ms; RTF 1.3979; partial/stable not measured | CPU peak 408.38% across cores; RAM peak 770.94 MiB; GPU/VRAM unavailable | Development evidence only; accuracy and RTF do not support an unqualified production claim |

No OpenAI accuracy/cost result was generated because API key, billing approval,
and cloud-dataset consent were not all available. The documented pricing
catalogue remains separate from accuracy.

## Release profiles

| Profile | Default | Providers | Credentials/consent | Intended use |
|---|---|---|---|---|
| `development-local` | Yes | local live + local final | No cloud credential | Safe developer default; existing feature flags remain default-off |
| `production-local` | No | local live + local final | Production auth/TLS/origin/model configuration; no cloud credential | Local-only deployment after remaining acceptance gates pass |
| `production-hybrid` | No | explicitly OpenAI live + final, local capability retained | Server-side key and external-audio consent required | Hybrid deployment only after paid/provider and privacy acceptance |

`config/release-profiles.json` is validated at API startup. Examples live under
`config/releases/`. Production templates intentionally contain rejected
placeholders so an operator must supply real secrets, origins, and checkpoints.

## Model and component disclosure

| Component | Functionality/accuracy status | Privacy | Hardware | License/pricing |
|---|---|---|---|---|
| Local Whisper `base` | Live/segment and accurate-final ASR; synthetic internal metrics above | Audio stays local | CPU supported, CUDA optional; measured CPU/RAM above | MIT model/code lineage as documented by repository; no per-request fee, infrastructure costs apply |
| Marian `Helsinki-NLP/opus-mt-id-en` | Stable preview/final translation lifecycle passes; real-model Stage 18 E2E not run | Transcript stays local | CPU/CUDA; checkpoint/cache required | Model-specific Hugging Face license must be reviewed; no per-request fee, infrastructure costs apply |
| SpeechBrain ECAPA-TDNN | Speaker lifecycle/clustering tests pass; real-model Stage 18 E2E not run; overlap separation unsupported | Segment audio/embeddings stay local | CPU/CUDA; checkpoint/cache required | Apache-2.0 SpeechBrain code plus checkpoint terms; no per-request fee, infrastructure costs apply |
| Rule-based glossary/quality/post-processing | Deterministic lifecycle and protection tests pass; not an acoustic/semantic accuracy model | Data stays local | Negligible relative compute | Repository code; no per-request fee |
| OpenAI transcription models | Optional API-only live/final integration; no internal Stage 18 accuracy run | Explicitly sends audio externally | Provider-managed compute and network | Proprietary API service; dated prices in `config/openai-pricing.json` |

The full model capability, privacy, hardware, license/service, pricing, and known
limitations catalogue remains in `docs/model-catalogue.md`.

## Reproduce

```powershell
services/api/.venv/Scripts/python.exe acceptance/stage18/run.py --run-local-benchmark
```

Manual gates can only be marked after evidence is reviewed:

```powershell
$env:STAGE18_MICROPHONE_E2E_RESULT="pass"
$env:STAGE18_API_RESTART_RESTORE_RESULT="pass"
$env:STAGE18_DEPLOYMENT_SECURITY_RESULT="pass"
```

Those variables record operator assertions; supporting logs/screenshots and
deployment identifiers must still be retained outside this non-sensitive
repository. OpenAI remains independently gated by `OPENAI_API_KEY`,
`OPENAI_BILLING_APPROVED`, and `BENCHMARK_CLOUD_DATA_APPROVED`.

## Exit criteria for re-evaluation

- Complete physical microphone scenarios for both transports and PCM + VAD.
- Run actual Marian and SpeechBrain checkpoints on the reviewed fixtures,
  including multi-speaker and overlap limitations.
- Restart the API during an active persisted session and verify Mongo/storage
  restore without duplicated segments.
- Execute production TLS/WSS, trusted-origin, ownership, secret-management,
  retention, backup/recovery, and monitoring-redaction acceptance.
- Record partial/stable/accurate-final/translation/diarization/end-to-end latency,
  queue depth, drop/duplicate/fallback rate, and GPU/VRAM where applicable.
- Run OpenAI only after all three explicit cloud gates, then record usage/cost.

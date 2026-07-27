# Stage 1 baseline report

Baseline date: 2026-07-27
Dataset version: `0.1.0-placeholder`
Implemented development default: local PyTorch Whisper `base`
Checkpoint SHA-256 metadata: `ed3a0b6b1c0edf879ad9b11b1af5a0e6ab5db9205f891f668f8b0e6c6326e34e`

## Outcome

The baseline is **not measured**. The repository contained no approved,
non-sensitive benchmark audio or reviewed reference transcripts/translations.
All eight manifest cases are deliberately disabled, and no accuracy, latency,
or resource values were fabricated. Although `storage/models/whisper/base.pt`
was present, the active Python 3.14 environment did not contain PyTorch or
psutil, so it was not a valid inference environment.

Machine-readable empty-run outputs are committed at:

- `benchmarks/results/baseline/results.json`
- `benchmarks/results/baseline/results.csv`
- `benchmarks/results/baseline/report.md`

## Acceptance metric status

| Metric | Initial target | Baseline | Reason |
|---|---:|---:|---|
| First partial transcript | < 1 s | Not measured | No enabled corpus; existing live result is complete-chunk output |
| Stable transcript | < 2 s | Unsupported / not measured | Application has no stable event/state |
| Live translation after stable | < 1 additional s | Unsupported / not measured | No stable state or live translation path |
| Final local result after endpoint | < 5 s | Not measured | Current stop operation copies partial text; no accurate final inference |
| WER | Dataset-relative | Not measured | No reviewed reference transcript |
| CER | Dataset-relative | Not measured | No reviewed reference transcript |
| Translation evaluation | Reference + provider output + human review | Not measured | No reviewed translation references |
| Real-time factor | Hardware-relative | Not measured | No inference run |
| CPU / RAM | Recorded | Not measured | No inference run; psutil absent |
| GPU / VRAM | Recorded | Not measured | No inference run; `nvidia-smi` availability not established for a run |
| Lost accepted audio chunks | 0 | Not provable | No sequence-number acknowledgement metric |
| Duplicate final segments | 0 | Not measured | No benchmark session and no revision identity |
| Cloud calls in local profile | 0 | ASR local only; translation fails criterion | GoogleTranslator is the only implemented translation provider |

## Reproduction gate

Before publishing a numeric baseline:

1. Populate every required profile with synthetic, licensed, or explicitly
   consented audio and reviewed UTF-8 references.
2. Set `contains_sensitive_data` to `false`, record provenance/licence, calculate
   SHA-256, and enable each reviewed manifest case.
3. Use a supported Python environment with the application dependencies and
   `benchmarks/requirements.txt` installed. Record GPU driver/runtime details.
4. Run the current provider adapter with the exact checkpoint digest and retain
   all three outputs without hand-editing numeric fields.
5. Have a human review translation output; automatic translation scoring is
   intentionally left `null` in Stage 1.

See `docs/architecture-audit.md`, `docs/local-first-gaps.md`, and
`docs/model-catalogue.md` for the evidence and limitations behind this report.

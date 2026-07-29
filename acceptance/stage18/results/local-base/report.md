# Benchmark report

- Run ID: `20260729T225820Z`
- Dataset: `stage15-safe-synthetic-benchmark` version `1.0.0`
- Provider/model: `local-whisper` / `base`
- Deployment: `local`
- Started: 2026-07-29T22:58:20.076588+00:00
- Cases: 7

| Case | Status | WER | CER | Partial ms | Stable ms | Final ms | Load ms | RTF | CPU peak % | RAM peak MiB | GPU peak % | VRAM peak MiB |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| id_quiet_001 | completed | 0.667 | 0.143 | n/a | n/a | 5504.312 | 676.431 | 1.582 | 408.378 | 727.523 | n/a | n/a |
| en_quiet_001 | completed | 0.000 | 0.000 | n/a | n/a | 5119.945 | 674.222 | 1.559 | 380.815 | 726.141 | n/a | n/a |
| code_switch_001 | completed | 0.778 | 0.308 | n/a | n/a | 6361.026 | 686.314 | 1.554 | 379.474 | 751.629 | n/a | n/a |
| far_field_001 | completed | 0.750 | 0.302 | n/a | n/a | 5708.618 | 675.016 | 1.385 | 393.057 | 770.941 | n/a | n/a |
| background_noise_001 | completed | 1.000 | 0.340 | n/a | n/a | 5782.203 | 692.956 | 1.412 | 380.529 | 757.477 | n/a | n/a |
| multiple_speakers_001 | completed | 0.167 | 0.087 | n/a | n/a | 5487.094 | 699.737 | 0.901 | 374.819 | 749.129 | n/a | n/a |
| overlapping_speech_001 | completed | 0.333 | 0.116 | n/a | n/a | 5580.009 | 685.071 | 1.392 | 369.688 | 752.551 | n/a | n/a |

## Limitations

- nvidia-smi unavailable; GPU and VRAM were not sampled
- provider emitted no partial event
- provider emitted no stable event

## Errors

- None reported.

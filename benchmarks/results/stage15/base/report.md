# Benchmark report

- Run ID: `20260729T134717Z`
- Dataset: `stage15-safe-synthetic-benchmark` version `1.0.0`
- Provider/model: `local-whisper` / `base`
- Deployment: `local`
- Started: 2026-07-29T13:47:17.430658+00:00
- Cases: 7

| Case | Status | WER | CER | Partial ms | Stable ms | Final ms | Load ms | RTF | CPU peak % | RAM peak MiB | GPU peak % | VRAM peak MiB |
|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| id_quiet_001 | completed | 0.667 | 0.143 | n/a | n/a | 6787.488 | 837.784 | 1.951 | 364.706 | 727.070 | n/a | n/a |
| en_quiet_001 | completed | 0.000 | 0.000 | n/a | n/a | 6248.755 | 759.603 | 1.895 | 317.285 | 753.070 | n/a | n/a |
| code_switch_001 | completed | 0.778 | 0.308 | n/a | n/a | 7597.228 | 766.254 | 1.850 | 353.994 | 731.578 | n/a | n/a |
| far_field_001 | completed | 0.750 | 0.302 | n/a | n/a | 8030.057 | 842.165 | 1.923 | 363.700 | 718.539 | n/a | n/a |
| background_noise_001 | completed | 1.000 | 0.340 | n/a | n/a | 7261.673 | 736.932 | 1.780 | 334.725 | 721.465 | n/a | n/a |
| multiple_speakers_001 | completed | 0.167 | 0.087 | n/a | n/a | 7685.445 | 779.591 | 1.263 | 334.025 | 750.410 | n/a | n/a |
| overlapping_speech_001 | completed | 0.333 | 0.116 | n/a | n/a | 6546.856 | 762.555 | 1.625 | 315.727 | 729.906 | n/a | n/a |

## Limitations

- nvidia-smi unavailable; GPU and VRAM were not sampled
- provider emitted no partial event
- provider emitted no stable event

## Errors

- None reported.

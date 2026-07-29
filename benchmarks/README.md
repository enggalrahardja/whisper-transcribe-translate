# Reproducible benchmark suite

This directory is isolated from the application runtime. It does not change the
API, UI, database schema, or provider selection.

## Dataset policy

Only use audio that is synthetic, explicitly recorded for this benchmark with
consent, or distributed under a compatible licence. Never copy production
uploads, customer meetings, credentials, personal data, or other sensitive
audio here.

Each enabled case in `dataset/manifest.json` must have:

- an audio file under `dataset/audio/`;
- a UTF-8 verbatim transcript under `dataset/references/transcripts/`;
- a UTF-8 reference translation under `dataset/references/translations/` when
  translation is evaluated;
- provenance, consent/licence, and an SHA-256 digest in the manifest.

The Stage 15 manifest contains repository-authored synthetic fixtures covering
all required profiles. References were checked against the exact synthesis
scripts. These fixtures are reproducible smoke/relative-comparison data, not a
substitute for a separately consented natural-speech evaluation corpus.

## Provider event contract

The runner executes one provider command per case. The command receives these
environment variables: `BENCHMARK_AUDIO`, `BENCHMARK_LANGUAGE`,
`BENCHMARK_TARGET_LANGUAGE`, `BENCHMARK_MODEL`, and `BENCHMARK_CASE_ID`.
It must write one JSON object per line to stdout. Supported events are:

```json
{"event":"partial","text":"provisional text"}
{"event":"stable","text":"stable source text"}
{"event":"translation","text":"translated text"}
{"event":"final","text":"final source text","translation":"optional final translation"}
```

Latency is measured when the runner receives the first event of each kind. A
provider that only supports offline transcription may emit only `final`; the
unsupported partial/stable metrics are stored as `null`, not zero.

Metric definitions:

- WER/CER use Unicode NFKC, case folding, punctuation removal, and whitespace
  normalization. CER excludes normalized spaces. Rates may exceed 1.0.
- Partial/stable latency is provider start to the first corresponding event.
- Final latency is `audio_end` to the first final event. If `audio_end` is not
  emitted, provider start is used and `final_latency_origin` plus a limitation
  make that fallback explicit.
- Real-time factor is provider wall time divided by decoded audio duration.
- CPU percentage and RAM RSS cover the provider process tree when `psutil` is
  installed. NVIDIA GPU utilization and VRAM are machine-wide samples from
  `nvidia-smi`, so concurrent GPU workloads invalidate those values.
- Translation evaluation preserves the final source input, reference output,
  and provider output. Stage 1 deliberately does not claim an automatic score;
  human review remains pending.

## Commands

Validate the suite without audio or model dependencies:

```powershell
python benchmarks/run.py validate
python -m unittest discover -s benchmarks/tests -v
```

Run enabled cases with a provider command (quote it as one argument):

```powershell
python benchmarks/run.py run `
  --provider local-whisper `
  --model base `
  --model-version openai-whisper-checkpoint `
  --deployment local `
  --provider-command "python path/to/provider_adapter.py" `
  --output-dir benchmarks/results/local-base
```

Stage 15 reproducible run and comparison collection:

```powershell
python benchmarks/run.py validate
python benchmarks/run.py run --provider local-whisper --model base --model-version base.pt `
  --deployment local --provider-command "python benchmarks/providers/current_whisper.py" `
  --beam-size 5 --output-dir benchmarks/results/stage15/base
python benchmarks/stage15.py --output-dir benchmarks/results/stage15
```

Repeat the runner command with `small`, `medium`, `large-v3`, and
`large-v3-turbo` only after the exact checkpoint is present. The collector
records missing checkpoints and local translation/diarization dependencies as
unsupported instead of silently substituting a model.

Install `psutil` in the benchmark environment for child-process CPU/RAM
sampling. NVIDIA GPU/VRAM sampling uses `nvidia-smi` when available. Missing
samplers are reported as limitations. The runner writes `results.json`,
`results.csv`, and `report.md` atomically to the selected output directory.

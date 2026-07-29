# Stage 17 — optional OpenAI transcription provider

## Outcome and defaults

The provider boundary supports `local` and `openai` for live and accurate-final
transcription. Both selectors default to `local`; legacy recording, local
Whisper `base`, PCM/VAD, semantic states, and existing feature flags keep their
defaults. OpenAI is API-only and is never downloaded.

OpenAI can start only when explicitly selected, `OPENAI_API_KEY` is set, and
`OPENAI_EXTERNAL_AUDIO_CONSENT=true`. Production still applies all Stage 16
security validation, and the `Private` profile rejects cloud providers. The key
is read only by the API process, excluded from events/persistence, and attached
only to server-to-OpenAI requests.

## Provider flow

```text
canonical PCM16 mono 16 kHz
  ├─ local (default) → local VAD → local live Whisper
  └─ openai (explicit) → boundary resample to PCM16 24 kHz
                       → server-side Realtime WebSocket
                       → existing partial/stable/final revision registry

completed VAD segment WAV
  ├─ local (default) → persistent local accurate-final worker
  └─ openai (explicit) → Audio Transcriptions API
                       → existing accurate-final lifecycle/replacement
```

Realtime uses `input_audio_buffer.append` and explicit commit at the existing
VAD segment boundary. Provider item IDs are associated with canonical sequence
ranges. Revisions remain monotonic, completed items are immutable, reconnect
keeps the server-side provider session, and the semantic UI contract is
unchanged. The live page displays the server-issued external-audio privacy
warning when cloud live transcription is selected.

Accurate-final sends a complete VAD WAV plus optional glossary prompt. Metadata
includes provider/model, request ID when returned, language, duration, usage,
latency, estimated cost, and retry count. Raw and glossary-corrected output stay
separate. Failed cloud jobs cannot delete the live transcript.

## Configuration

```dotenv
LIVE_TRANSCRIPTION_PROVIDER=local
LIVE_FINAL_PROVIDER=local
OPENAI_API_KEY=
OPENAI_LIVE_MODEL=gpt-realtime-whisper
OPENAI_FINAL_MODEL=gpt-4o-transcribe
OPENAI_TIMEOUT_SECONDS=30
OPENAI_MAX_RETRIES=2
OPENAI_RATE_LIMIT_PER_MINUTE=30
OPENAI_EXTERNAL_AUDIO_CONSENT=false
OPENAI_ALLOW_LOCAL_FALLBACK=false
```

OpenAI live additionally requires PCM streaming, VAD, and semantic transcript
state. Optional final IDs are `gpt-4o-mini-transcribe` and
`gpt-4o-transcribe-diarize`. Work is bounded by the existing final queue and by
provider timeout, retry, and per-process request limits.

Fallback is asymmetric: OpenAI → local is allowed only with
`OPENAI_ALLOW_LOCAL_FALLBACK=true`; local → OpenAI never happens automatically.
With fallback disabled, failure is reported while prior live/local output stays
available.

## Pricing and benchmark

Pricing is read from `config/openai-pricing.json`, including billing unit,
prices, currency, official source, and checked date. Realtime duration estimates
are available before a call. Token-priced final models need returned usage for
a meaningful estimate. Estimates are not invoices.

The cloud adapter uses the same Stage 15 runner and refuses execution unless all
three gates are explicit:

```powershell
$env:OPENAI_API_KEY="..."
$env:OPENAI_BILLING_APPROVED="true"
$env:BENCHMARK_CLOUD_DATA_APPROVED="true"
python benchmarks/run.py run --provider openai --model gpt-4o-transcribe `
  --model-version api-alias --deployment cloud `
  --provider-command "python benchmarks/providers/openai_transcription.py" `
  --output-dir benchmarks/results/openai-gpt-4o-transcribe
```

No cloud benchmark was run: credentials, billing approval, and dataset cloud
consent were not provided. No internal accuracy claim or “best” label is made.

## Deployment checklist

- Confirm audio may leave the deployment and record consent.
- Store `OPENAI_API_KEY` server-side; never use a `NEXT_PUBLIC_*` key.
- Validate account model access, spend/rate limits, outbound TLS, retry budget,
  pricing date, and local fallback capacity.
- Test reconnect, provider failure, persistence, and cost monitoring before
  enabling production cloud traffic.

## Known limitations

- Availability, rate limits, retention/data controls, billed usage, and aliases
  are external dependencies.
- Stable is mapped when the provider marks a stable event/delta; completed is
  the only immutable provider event.
- Rich output from the optional diarize model is not yet merged into Stage 9.
- Provider rate limiting is in-process, not shared across API replicas.

Official references checked 2026-07-30:

- https://developers.openai.com/api/docs/guides/realtime-transcription
- https://developers.openai.com/api/docs/models/gpt-realtime-whisper
- https://developers.openai.com/api/docs/models/gpt-4o-transcribe
- https://developers.openai.com/api/docs/models/gpt-4o-mini-transcribe
- https://developers.openai.com/api/docs/models/gpt-4o-transcribe-diarize

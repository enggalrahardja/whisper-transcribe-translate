# Stage 8 — final translation quality pass

## Status and scope

Stage 8 is implemented as local deterministic rules behind
`LIVE_TRANSLATION_QUALITY_ENABLED=false` and
`NEXT_PUBLIC_LIVE_TRANSLATION_QUALITY_ENABLED=false`. Its only accepted runtime
input is a Stage 7 translation with status `completed`. Stable translation
preview, source transcript, audio, transcription state, and legacy flows are
not modified.

No additional inference model, LLM, OpenAI client, or cloud provider is used.
The processor is repository rule-based code under the repository license, has
no per-request fee, and needs no GPU. Infrastructure operation still has a
cost. Accuracy remains **Unclassified** until internal human translation review
is available; the quality pass is not a semantic translation model.

## Data flow and state

```text
Stage 7 preview --------------------------------------> unchanged UI preview
Stage 7 completed translation
  -> bounded quality queue
  -> pending -> processing -> completed | failed
  -> deterministic terminology and formatting rules
  -> safety postconditions
  -> translation_quality_state / reconnect snapshot
  -> corrected final translation or raw-final fallback
```

The event stores three distinct values:

- `rawModelTranslation`: Marian output before Stage 7 glossary restoration.
- `rawTranslation`: completed Stage 7 translation used as quality input.
- `correctedTranslation`: Stage 8 output; equal to `rawTranslation` on failure.

`appliedCorrections` records the rule plus before/after text. Quality status is
`pending`, `processing`, `completed`, or `failed`. Jobs are idempotent by
session, segment, translation revision, and final-translation digest. Runtime
state is isolated by session and restores the latest revision on reconnect.

## Deterministic correction order

1. Enforce applicable glossary `preferredTranslations` and `doNotTranslate`
   forms only when the source contains that configured term.
2. Replace recognized dates, times, versions, product-style codes, numbers,
   units, and speaker attributions with internal markers.
3. Collapse Unicode whitespace and trim boundaries.
4. Normalize punctuation spacing/repetition and add final punctuation when
   absent.
5. Remove only consecutive exact duplicate sentences or exact repeated
   two-to-eight-word phrases.
6. Capitalize sentence starts.
7. Restore every protected value verbatim and run safety postconditions.

Applying the processor again to corrected output produces the same text and no
new corrections.

## Safety constraints

- Source transcript and preview translation are never quality-job outputs.
- Audio `startMs`/`endMs`, source language, target language, and glossary
  version are copied unchanged into every quality event.
- Dates, times, numbers, codes, versions, and recognized speaker prefixes are
  protected before general formatting.
- Digit multisets must match the raw final translation after correction.
- Listed Indonesian/English negation token counts must match.
- Recognized `Speaker …:` / `Pembicara …:` attributions must match exactly.
- Rules are allow-listed transformations and do not generate new semantic
  clauses. A failed postcondition rejects the candidate rather than attempting
  a speculative repair.
- Any exception, timeout, or safety rejection produces `failed` with
  `fallback=true` and preserves `rawTranslation` as displayed output.

## Configuration

| Variable | Default |
|---|---|
| `LIVE_TRANSLATION_QUALITY_ENABLED` | `false` |
| `NEXT_PUBLIC_LIVE_TRANSLATION_QUALITY_ENABLED` | `false` |
| `LIVE_TRANSLATION_QUALITY_TIMEOUT_SECONDS` | `2` |
| `LIVE_TRANSLATION_QUALITY_MAX_RETRIES` | `1` |
| `LIVE_TRANSLATION_QUALITY_WORKER_CONCURRENCY` | `1` |
| `LIVE_TRANSLATION_QUALITY_QUEUE_CAPACITY` | `64` |

The backend quality flag is effective only when the PCM semantic translation
pipeline is enabled. Frontend and backend flags should be enabled together
after end-to-end validation.

## Metrics

Runtime metrics include processed quality jobs, applied corrections, failed
jobs, average correction latency, fallback count, terminology corrections,
number/date/code protection count, retries, queue depth, duplicate discard, and
out-of-order rejection.

## Known limitations

- Repetition removal is lexical and consecutive; paraphrased repetition is not
  removed.
- Capitalization is sentence-boundary based and does not infer proper nouns
  outside the glossary.
- Date/code recognition covers common patterns, not every locale or identifier.
- Negation and speaker validation uses an explicit pattern list rather than
  full semantic parsing.
- The pass cannot detect hallucination, repair missing translation content, or
  prove semantic equivalence/no-information-addition beyond its allow-listed
  transforms and invariants.
- Runtime queue/state is process-local and is lost on API restart.
- Interactive microphone E2E and internal human quality evaluation remain
  pending.

# Stage 10 — transcript post-processing

## Status and boundaries

Stage 10 is a runtime-only deterministic rule layer behind
`LIVE_TRANSCRIPT_POSTPROCESS_ENABLED=false` and
`NEXT_PUBLIC_LIVE_TRANSCRIPT_POSTPROCESS_ENABLED=false`. It accepts semantic
`final` revisions and controlled accurate-final replacements only. Partial and
stable revisions, legacy recorder output, raw model output, translation,
diarization, and audio timestamps are never modified.

No model, LLM, OpenAI client, or cloud provider is added. Accuracy is
**Unclassified** pending internal transcript review; formatting rules cannot
repair acoustic recognition mistakes.

## Output contract and priority

Each runtime result stores separately:

- `rawTranscript`: exact model output retained by the transcription layer.
- `glossaryCorrectedTranscript`: final transcript before Stage 10.
- `postProcessedTranscript`: deterministic candidate, or the unchanged
  glossary-corrected value on failure.
- `appliedCorrections`: ordered rule name plus before/after values.

Live final revision 3 can create an initial job. When an accurate-final
replacement is accepted at revision 4, its job supersedes the earlier result
for the same session and segment. Duplicate job IDs are idempotent and stale
revisions are rejected. Reconnect restores only the latest result per segment.

## Rule order

1. Re-apply the captured glossary snapshot without recording glossary metrics.
2. Collapse whitespace.
3. Normalize conservative numeric grouping such as `1 000` to `1,000`.
4. Normalize numeric ISO-like dates (`YYYY/MM/DD`) and dot times (`09.30`).
5. Protect invariant tokens with internal markers.
6. Preserve fillers, or remove configured whole-word fillers.
7. Normalize punctuation and remove consecutive exact repeated phrases.
8. Capitalize sentence starts.
9. Split deterministic paragraphs after the configured sentence count.
10. Restore protected tokens and validate safety invariants.

The same input/configuration produces the same output. Reprocessing the output
is idempotent.

## Protection and safety

URLs, emails, product/model codes, versions, acronyms, existing numbers,
dates/times, glossary terminology, listed negations, and speaker attribution
are marker-protected during general text rules. Digit multisets and exact
URL/email/version/code/negation/speaker values are checked afterward. Sequence
and audio timestamps are copied unchanged. Any exception, timeout, or failed
postcondition returns `failed`, `fallback=true`, and preserves the
glossary-corrected transcript.

This is conservative syntax processing. It does not infer missing facts,
rewrite meaning, resolve ambiguous spoken-number words, or add content.

## Filler modes

| Mode | Behavior |
|---|---|
| `preserve` | Default; filler words remain untouched. |
| `remove` | Removes only configured whole-word fillers and then repairs adjacent whitespace/punctuation. |

The default configured list is `uh,um,erm,hmm,eh,anu,eee,mmm`. Domain words
that may be meaningful should not be added without corpus validation.

## Queue and configuration

Jobs use `pending`, `processing`, `completed`, and `failed`. Capacity,
concurrency, retry, and timeout are bounded. State is process-local and is not
written to the production schema.

| Variable | Default |
|---|---|
| `LIVE_TRANSCRIPT_POSTPROCESS_ENABLED` | `false` |
| `NEXT_PUBLIC_LIVE_TRANSCRIPT_POSTPROCESS_ENABLED` | `false` |
| `LIVE_TRANSCRIPT_POSTPROCESS_FILLER_MODE` | `preserve` |
| `LIVE_TRANSCRIPT_POSTPROCESS_FILLER_WORDS` | `uh,um,erm,hmm,eh,anu,eee,mmm` |
| `LIVE_TRANSCRIPT_POSTPROCESS_PARAGRAPH_SENTENCES` | `3` |
| `LIVE_TRANSCRIPT_POSTPROCESS_TIMEOUT_SECONDS` | `2` |
| `LIVE_TRANSCRIPT_POSTPROCESS_MAX_RETRIES` | `1` |
| `LIVE_TRANSCRIPT_POSTPROCESS_WORKER_CONCURRENCY` | `1` |
| `LIVE_TRANSCRIPT_POSTPROCESS_QUEUE_CAPACITY` | `64` |

Metrics expose jobs, completed, failed, retries, correction count, duplicate
phrases removed, fillers handled, protected tokens, average processing latency,
fallback count, queue depth, duplicates, and stale revisions.

## Known limitations

- Paragraph boundaries use sentence count rather than discourse understanding.
- Filler removal cannot infer whether a configured filler is meaningful in a
  particular utterance.
- Number/date/time normalization is deliberately narrow and does not convert
  general spoken-number phrases.
- Safety checks protect recognized token patterns; they are not a semantic
  equivalence proof.
- Runtime results survive WebSocket reconnect within one API process but not a
  server restart.
- Automated rule/queue tests pass; microphone E2E and internal transcript
  quality review remain pending.


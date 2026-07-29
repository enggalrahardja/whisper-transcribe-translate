# Stage 6: Local glossary and terminology control

Status: implemented for PCM + VAD semantic live and accurate-final paths behind
`LIVE_GLOSSARY_ENABLED=false`. Legacy and translation paths are unchanged.

## Glossary structure

The development seed is `config/glossary.development.json` and contains
`SMARTHub`, `RFID Core`, `TagTrace`, `FX9600`, `Galva Technologies`, and `CIS`.
Each entry uses:

```json
{
  "preferredSpelling": "SMARTHub",
  "aliases": ["Smart Hub", "SmartHub"],
  "doNotChange": false,
  "doNotTranslate": true,
  "preferredTranslations": {"en": "SMARTHub"},
  "category": "product",
  "priority": 100,
  "language": "*",
  "active": true
}
```

`language` accepts a language/tag or `*`; `auto` sessions consider all active
languages. Inactive entries are not loaded into a snapshot.
`doNotTranslate` and `preferredTranslations` are optional Stage 7 extensions:
they protect terminology or select an exact target-language form without
changing Stage 6 transcript correction behavior.

## Per-segment flow

```text
new VAD segment -> immutable glossary snapshot/version
  |-> terminology prompt -> live local Whisper -> raw output -> deterministic correction
  \-> same snapshot in accurate-final job -> raw output -> deterministic correction
```

The snapshot is captured before live inference and stored directly in the
accurate-final request. Editing and reloading the file therefore affects only
segments that start after reload. It never changes earlier segments
retroactively and does not reload either Whisper model.

## Matching and priority rules

- Terms and aliases are recognized case-insensitively, but a correction writes
  the exact preferred spelling.
- Both sides require Unicode word boundaries; substrings inside a larger word
  are not replaced.
- Punctuation and whitespace are valid boundaries. Literal term punctuation is
  escaped rather than interpreted as a regular expression.
- Candidates are resolved by highest priority, then longest span, then source
  position. Rejected overlaps increment the conflict metric.
- `doNotChange` accepts/protects the matched span as-is and can block a
  lower-priority overlapping correction.
- Applying correction to already-corrected output produces no new correction,
  making post-correction idempotent.

This is deterministic text correction, not blind substring replacement.

## Raw versus corrected output

Semantic transcript updates retain `rawText`, corrected `text`,
`glossaryCorrections`, and `glossaryVersion`. Each correction records source,
replacement, raw character offsets, category, priority, and language.
Accurate-final job results retain the same fields. Timestamp start/end values
are copied from the audio/model output and are never shifted by text length.

## Prompt context

Up to `LIVE_GLOSSARY_PROMPT_MAX_TERMS` active entries, sorted by priority, are
combined with the existing live prompt. The same local context is passed to the
accurate-final Whisper call. Prompting is advisory; deterministic correction
is still applied afterward.

## Configuration and reload

| Variable | Default | Meaning |
| --- | --- | --- |
| `LIVE_GLOSSARY_ENABLED` | `false` | Enables glossary only on the PCM + VAD semantic path |
| `LIVE_GLOSSARY_PATH` | `config/glossary.development.json` | Local JSON glossary file |
| `LIVE_GLOSSARY_PROMPT_MAX_TERMS` | `64` | Maximum active terms included in model context |

`POST /api/live/glossary/reload` parses and atomically swaps the runtime
snapshot. When disabled, reload returns disabled state without reading the
file. Invalid enabled files fail reload and leave the previous snapshot active.

## Metrics

Runtime metrics expose terms loaded, corrections applied, corrected segments,
unmatched configured aliases, average correction latency, reload count, and
overlap conflicts. `unmatched_aliases` counts applicable configured aliases not
seen in each correction call; it is a tuning signal rather than an accuracy
score.

## Limitations and validation

- Glossary correction cannot recover speech content the acoustic model did not
  recognize closely enough to match a configured alias.
- Prompt context is bounded and Whisper is not a true token-streaming model.
- Glossary state and metrics are process-local and do not survive restart.
- Automated spelling, alias, whole-word, case, priority, protection,
  punctuation, idempotency, reload, isolation, raw/corrected, prompt, timestamp,
  and default-off tests pass. Interactive microphone E2E and terminology corpus
  evaluation remain pending, so the feature remains disabled by default.

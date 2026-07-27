# Stage 4: Live streaming transcription state

Status: implemented as runtime state for the PCM + VAD path behind
`LIVE_TRANSCRIPT_STATE_ENABLED=false` and
`NEXT_PUBLIC_LIVE_TRANSCRIPT_STATE_ENABLED=false`. The legacy path is unchanged.

## Flow and state machine

```text
PCM ACK -> ordered PCM buffer -> local VAD -> finalized speech segment
    -> existing local Whisper model -> partial -> stable -> final
                                      \-> runtime snapshot for reconnect
```

The states have these semantics:

- `partial` is mutable working text and is never appended permanently by the UI.
- `stable` is consistent text. A later stable revision may extend it; shortening
  requires an explicit server-side rollback reason.
- `final` replaces the working state for the same segment and is immutable.

Every accepted event replaces the previous runtime entry for its
`sessionId`/`segmentId`. This keeps the UI from rendering the same segment in
multiple state buckets.

## WebSocket event contract

`transcript_state` contains these fields at the top level:

```json
{
  "type": "transcript_state",
  "sessionId": "...",
  "segmentId": "pcm-10-24",
  "revision": 3,
  "state": "final",
  "sequenceStart": 10,
  "sequenceEnd": 24,
  "startMs": 2000,
  "endMs": 5000,
  "text": "Example transcript",
  "language": "en",
  "model": "base",
  "latencyMs": 730,
  "metrics": {}
}
```

On `pcm_hello`, `transcript_state_snapshot` returns the latest accepted update
for every segment in that active session. State is isolated by session and
exists only in the API process; MongoDB and other production schemas are not
changed.

## Revision rules

- The first revision is `1`; each accepted revision must be exactly previous + 1.
- An identical current update is discarded as a duplicate.
- A skipped, old, or conflicting revision is rejected as out of order.
- State cannot regress from stable to partial or from final to another state.
- Once final is accepted, later revisions cannot change that segment.
- Stable text must retain its prior prefix unless the caller supplies an
  explicit rollback reason.

## UI behavior

When the frontend flag is enabled, the live page holds a map keyed by
`segmentId`, rejects duplicate/out-of-order client updates, and renders separate
partial, stable, and final views. A final update overwrites the same segment's
partial/stable entry. When the flag is disabled, the existing partial/final UI
and legacy audio behavior remain in use.

## Metrics

Each state event and reconnect snapshot includes runtime-only metrics:

- average partial, stable, and final latency;
- latest revision per segment;
- discarded duplicate updates;
- rejected out-of-order updates;
- finalized segments.

`latencyMs` currently measures the completed local Whisper call for the VAD
segment. All three semantic lifecycle states therefore initially share the same
inference latency.

## Feature flags

| Variable | Default | Purpose |
| --- | --- | --- |
| `LIVE_TRANSCRIPT_STATE_ENABLED` | `false` | Enables server semantic events only after PCM + VAD segment finalization |
| `NEXT_PUBLIC_LIVE_TRANSCRIPT_STATE_ENABLED` | `false` | Enables semantic-state rendering in the live page |

Both existing PCM and VAD flags must also be enabled for the new server path.
The local Whisper default remains `base`; no cloud provider was added.

## Limitations and validation status

Existing Whisper transcribes a completed audio window and is not a true token
streaming decoder. Stage 4 supplies the event contract, revision enforcement,
reconnect snapshot, metrics, and UI replacement semantics, but the initial
producer derives partial/stable/final sequentially from one completed VAD
segment result. It does not promise token-level partial timing.

Automated state, duplicate, ordering, final immutability, reconnect, session
isolation, and default-feature tests pass. TypeScript compilation passes.
Interactive microphone E2E and multi-process state recovery remain pending, so
the new flags remain off by default.

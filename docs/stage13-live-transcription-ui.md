# Stage 13 — live transcription UI

## State hierarchy

The existing `/live` page keeps both legacy and feature-flagged PCM behavior.
PCM semantic mode renders one primary block per `segmentId`; reconnect snapshots
merge by ID/revision and cannot append duplicates.

Source display precedence is `post-processed` → `accurate-final` → `final` →
`stable` → `partial`. Partial is dashed/mutable, stable is explicitly labelled,
and final-derived states use permanent styling. Translation remains in a
separate column with `quality-corrected` → `completed` → `preview` precedence.

Speaker label, stable ID, confidence, and rename are visible in the block
header. Glossary, accurate-final, transcript post-processing, translation
quality, failure, and fallback states are summarized without exposing large
technical payloads. Timing, revision, model, raw source, and translation model
are available through keyboard-accessible `<details>`.

## Controls and device state

Session controls retain backend-supported start, pause/resume, stop, and local
clear behavior. Clear resets only browser state and does not delete persistence.
Source/target language and microphone selectors have explicit labels. Device
enumeration reacts to `devicechange`; selected device is requested on start.
Input level, PCM/legacy transport, connection, and VAD state remain visible.
Controls use existing session-state disabling.

## Reconnect, empty, degraded, and errors

Loading/requesting, empty, reconnecting, error, and persistence-degraded states
have explicit text. Important low-frequency state uses polite live regions;
rapid partial text itself is not an aria-live region. Persistence degradation
does not hide or stop the transcript.

## Auto-scroll and responsiveness

Auto-scroll occurs only while the viewport is within 180 px of the document
bottom. Scrolling upward disables it and shows a sticky “new transcript” action;
the user explicitly returns to the latest result. No nested transcript scroller
is introduced. Source and translation use two columns on desktop and one column
below 760 px. Long text wraps and raw metadata stays collapsed.

## Feature flags and manual screenshot checklist

All Stage 2–12 flags remain default-off. Legacy mode retains its original
partial/final panels and controls. No backend contract, schema, provider, or
cloud integration is added.

Manual validation screenshots should cover:

- empty and microphone-requesting states;
- partial, stable, final, accurate-final, and post-processed source states;
- preview, completed, and quality-corrected translation;
- named/unnamed speakers and rename focus flow;
- processing failure/fallback and degraded persistence;
- reconnect restoration with no duplicate blocks;
- desktop two-column, tablet, and narrow mobile layouts;
- scrolled-up “new transcript” indicator and focus-visible keyboard navigation.


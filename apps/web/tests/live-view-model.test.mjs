import test from "node:test";
import assert from "node:assert/strict";
import { mergeByRevision, nearBottom, transcriptDisplay, translationDisplay, workspaceStatus } from "../lib/live-view-model.mjs";

test("segment revision replaces without duplication and reconnect remains idempotent", () => {
  const partial = { segmentId: "a", revision: 1, state: "partial", text: "one" };
  const final = { segmentId: "a", revision: 3, state: "final", text: "final" };
  let state = mergeByRevision({}, [partial]);
  state = mergeByRevision(state, [final, final]);
  assert.equal(Object.keys(state).length, 1);
  assert.equal(state.a.text, "final");
});

test("transcript display precedence", () => {
  const segment = { state: "final", text: "final" };
  const accurate = { status: "completed", update: { text: "accurate" } };
  assert.equal(transcriptDisplay(segment, accurate).text, "accurate");
  assert.equal(transcriptDisplay(segment, accurate, { status: "completed", postProcessedTranscript: "processed" }).text, "processed");
});

test("translation quality replaces completed and preview", () => {
  assert.equal(translationDisplay({ status: "preview", translatedText: "preview" }).state, "preview");
  assert.equal(translationDisplay({ status: "completed", translatedText: "final" }).text, "final");
  assert.equal(translationDisplay({ status: "completed", translatedText: "final" }, { status: "completed", correctedTranslation: "quality" }).text, "quality");
});

test("auto scroll only when near bottom", () => {
  assert.equal(nearBottom(780, 200, 1000), true);
  assert.equal(nearBottom(100, 200, 1000), false);
});

test("empty loading reconnecting degraded and error states", () => {
  assert.equal(workspaceStatus({ segmentCount: 0 }), "empty");
  assert.equal(workspaceStatus({ requesting: true, segmentCount: 0 }), "loading");
  assert.equal(workspaceStatus({ reconnecting: true, segmentCount: 1 }), "reconnecting");
  assert.equal(workspaceStatus({ degraded: true, segmentCount: 1 }), "degraded");
  assert.equal(workspaceStatus({ error: "failed", segmentCount: 1 }), "error");
});

import assert from "node:assert/strict";
import test from "node:test";
import { paragraphsForDisplay } from "../lib/transcript-view-model.mjs";

test("725 ordered segments are not rendered as one paragraph", () => {
  const segments = Array.from({ length: 725 }, (_, index) => ({
    id: index, start: index * 2, end: index * 2 + 2, text: `segment ${index}`,
  }));
  const paragraphs = paragraphsForDisplay(
    { segments, original_segments: segments },
    { processingMode: "interview", minimumSilenceMs: 800 },
  );
  assert.ok(paragraphs.length > 1);
  assert.equal(paragraphs.flatMap((paragraph) => paragraph.segment_ids).length, 725);
});

test("speaker changes and interview pauses create paragraph breaks", () => {
  const segments = [
    { id: 1, start: 0, end: 1, text: "satu", speaker_id: "speaker-1" },
    { id: 2, start: 1.1, end: 2, text: "dua", speaker_id: "speaker-2" },
    { id: 3, start: 3, end: 4, text: "tiga", speaker_id: "speaker-2" },
  ];
  assert.equal(paragraphsForDisplay(
    { segments }, { processingMode: "interview", minimumSilenceMs: 800 },
  ).length, 3);
});

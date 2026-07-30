import assert from "node:assert/strict";
import test from "node:test";
import { formatBrowserDate, paragraphsForDisplay } from "../lib/transcript-view-model.mjs";

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

test("paragraph confidence aggregates all member segments and exposes its status", () => {
  const segments = [
    { id: 1, start: 0, end: 1, text: "satu", confidence: 0.7, paragraph_id: "p-1" },
    { id: 2, start: 1, end: 2, text: "dua", confidence: 0.82, paragraph_id: "p-1" },
  ];
  const [paragraph] = paragraphsForDisplay({
    segments,
    paragraphs: [{ id: "p-1", start: 0, end: 2, text: "satu dua", segment_ids: [1, 2] }],
  });

  assert.equal(paragraph.confidence, 0.76);
  assert.equal(paragraph.confidence_status, "Medium");
});

test("UTC API timestamp is formatted in the selected browser timezone and locale", () => {
  const formatted = formatBrowserDate("2026-01-15T12:00:00", "en-US", "America/New_York");

  assert.match(formatted, /Jan 15, 2026/);
  assert.match(formatted, /7:00:00 AM/);
  assert.doesNotMatch(formatted, /12:00:00 PM/);
});

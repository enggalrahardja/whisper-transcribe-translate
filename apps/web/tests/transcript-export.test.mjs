import assert from "node:assert/strict";
import test from "node:test";

import {
  createTranscriptExport,
  DEFAULT_TRANSCRIPT_EXPORT_OPTIONS,
  formatTranscriptExport,
  transcriptExportFilename,
} from "../lib/transcript-export.mjs";

const paragraph = {
  id: "p-1",
  start: 0,
  end: 5.46,
  text: "Dan perusahaan Jepang, yang mau tarik balik ke Jepang itu apa perusahaan?",
  confidence: 0.76,
  confidence_status: "Medium",
};

test("all transcript export options default to off", () => {
  assert.deepEqual(DEFAULT_TRANSCRIPT_EXPORT_OPTIONS, {
    includeTimestamp: false,
    includeConfidenceValue: false,
    includeConfidenceStatus: false,
  });
});

test("default export contains paragraph text only", () => {
  assert.equal(formatTranscriptExport([paragraph]), paragraph.text);
});

test("timestamp can be enabled independently", () => {
  assert.equal(formatTranscriptExport([paragraph], {
    ...DEFAULT_TRANSCRIPT_EXPORT_OPTIONS,
    includeTimestamp: true,
  }), `[00:00:00.000 - 00:00:05.460]\n${paragraph.text}`);
});

test("confidence value can be enabled independently", () => {
  assert.equal(formatTranscriptExport([paragraph], {
    ...DEFAULT_TRANSCRIPT_EXPORT_OPTIONS,
    includeConfidenceValue: true,
  }), `${paragraph.text}\nConfidence: 76%`);
});

test("confidence status can be enabled independently", () => {
  assert.equal(formatTranscriptExport([paragraph], {
    ...DEFAULT_TRANSCRIPT_EXPORT_OPTIONS,
    includeConfidenceStatus: true,
  }), `${paragraph.text}\nConfidence status: Medium`);
});

test("all options produce the combined requested format", () => {
  assert.equal(formatTranscriptExport([paragraph], {
    includeTimestamp: true,
    includeConfidenceValue: true,
    includeConfidenceStatus: true,
  }), `[00:00:00.000 - 00:00:05.460]\n${paragraph.text}\nConfidence: 76% · Medium`);
});

test("export filename uses the original basename", () => {
  assert.equal(transcriptExportFilename("BD.wav"), "BD-transcript.txt");
  assert.equal(transcriptExportFilename("rekaman wawancara"), "rekaman wawancara-transcript.txt");
});

test("UTF-8 content and paragraph line breaks are preserved", () => {
  const paragraphs = [
    { ...paragraph, text: "Pembicara pertama berkata: jelas." },
    { ...paragraph, id: "p-2", start: 6, end: 8, text: "Lalu dibalas, ‘tidak masalah’." },
  ];
  const transcriptExport = createTranscriptExport("diskusi.wav", paragraphs);
  const encoded = new TextEncoder().encode(transcriptExport.content);
  const decoded = new TextDecoder("utf-8", { fatal: true }).decode(encoded);

  assert.equal(transcriptExport.mimeType, "text/plain;charset=utf-8");
  assert.equal(transcriptExport.filename, "diskusi-transcript.txt");
  assert.equal(decoded, `${paragraphs[0].text}\n\n${paragraphs[1].text}`);
});

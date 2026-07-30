import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const pageSource = readFileSync(new URL("../app/jobs/[jobId]/page.tsx", import.meta.url), "utf8");

test("raw segment lists only appear inside Technical Details", () => {
  const technicalDetailsStart = pageSource.indexOf('<details className="technical-details">');
  const technicalDetailsEnd = pageSource.indexOf("</details>", technicalDetailsStart);

  assert.ok(technicalDetailsStart > 0);
  assert.ok(technicalDetailsEnd > technicalDetailsStart);
  assert.doesNotMatch(pageSource.slice(0, technicalDetailsStart), /className="segment-list"/);
  assert.match(pageSource.slice(technicalDetailsStart, technicalDetailsEnd), /className="segment-list"/);
  assert.doesNotMatch(pageSource, /<h3>Segments<\/h3>/);
});

test("Technical Details is collapsed by default", () => {
  assert.match(pageSource, /<details className="technical-details">\s*<summary>Technical Details<\/summary>/);
  assert.doesNotMatch(pageSource, /<details className="technical-details"[^>]*\sopen(?:=|\s|>)/);
});

test("paragraphs render timestamp and aggregated confidence status", () => {
  assert.match(pageSource, /formatTimestamp\(paragraph\.start\).*formatTimestamp\(paragraph\.end\)/s);
  assert.match(pageSource, /Confidence \$\{Math\.round\(paragraph\.confidence \* 100\)\}% · \$\{paragraph\.confidence_status\}/);
});

test("job dates wait for client mount before browser-local formatting", () => {
  assert.match(pageSource, /browserFormattingReady \? formatBrowserDate\(job\.created_at\) : "—"/);
  assert.match(pageSource, /browserFormattingReady \? formatBrowserDate\(job\.started_at\) : "—"/);
  assert.match(pageSource, /browserFormattingReady \? formatBrowserDate\(job\.completed_at\) : "—"/);
});

test("primary transcript export button is labeled Save Transcript", () => {
  assert.match(pageSource, /onClick=\{openExportMenu\}[^>]*>\s*Save Transcript\s*<\/button>/);
  assert.doesNotMatch(pageSource, />\s*Copy transcript\s*</i);
});

test("download uses the displayed paragraph order", () => {
  assert.match(pageSource, /createTranscriptExport\(job\.file_name, filteredOriginalParagraphs, exportOptions\)/);
});

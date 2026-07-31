import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const historySource = readFileSync(new URL("../app/history/page.tsx", import.meta.url), "utf8");
const liveSource = readFileSync(new URL("../app/components/live-history.tsx", import.meta.url), "utf8");
const subtitleSource = readFileSync(new URL("../app/components/subtitle-history.tsx", import.meta.url), "utf8");
const paginationSource = readFileSync(new URL("../app/components/history-pagination.tsx", import.meta.url), "utf8");
const loadingSource = readFileSync(new URL("../app/components/history-loading.tsx", import.meta.url), "utf8");

test("every History table shows device type", () => {
  assert.match(historySource, /<th>Device type<\/th>/);
  assert.match(historySource, /\{job\.media_type\}/);
  assert.match(liveSource, /<th>Device type<\/th>/);
  assert.match(liveSource, /<td>microphone<\/td>/);
  assert.match(subtitleSource, /<th>Device type<\/th>/);
  assert.match(subtitleSource, /\{project\.media_type\}/);
});

test("every History table provides a delete action", () => {
  assert.match(historySource, /runAction\(job, "delete"\)/);
  assert.match(liveSource, /method: "DELETE"/);
  assert.match(liveSource, /deleteSession\(session\)/);
  assert.match(subtitleSource, /deleteProject\(project\)/);
});

test("History separates lists into tabs and provides new audio actions", () => {
  assert.match(historySource, /useState<HistoryTab>\("transcribe"\)/);
  assert.match(historySource, /\["transcribe", "Transcribe Audio"\]/);
  assert.match(historySource, /\["translate", "Translate Audio"\]/);
  assert.match(historySource, /\["live", "Live Sessions"\]/);
  assert.match(historySource, /\["subtitles", "Subtitle Projects"\]/);
  assert.match(historySource, /job\.task === activeTab/);
  assert.match(historySource, /<Link href=\{`\/\$\{activeTab\}`\}>Start new \{activeTab\} audio<\/Link>/);
});

test("every History table has row numbers and multiple selection", () => {
  for (const source of [historySource, liveSource, subtitleSource]) {
    assert.match(source, /<th>No\.<\/th>/);
    assert.match(source, /type="checkbox"/);
    assert.match(source, /Delete selected/);
    assert.match(source, /Promise\.allSettled/);
  }
});

test("every History tab uses configurable paging with complete navigation", () => {
  for (const source of [historySource, liveSource, subtitleSource]) {
    assert.match(source, /<HistoryPagination/);
    assert.match(source, /pageSize=\{pageSize\}/);
    assert.match(source, /onPageChange=\{setPage\}/);
    assert.match(source, /onPageSizeChange=\{setPageSize\}/);
  }
  assert.match(paginationSource, /Rows per page/);
  assert.match(paginationSource, />First<\/button>/);
  assert.match(paginationSource, />Prev<\/button>/);
  assert.match(paginationSource, />Next<\/button>/);
  assert.match(paginationSource, />Last<\/button>/);
  assert.match(paginationSource, /\[5, 10, 20, 50\]/);
  assert.match(paginationSource, /Math\.min\(7, totalPages\)/);
  assert.match(paginationSource, /visiblePages\.map/);
  assert.match(paginationSource, /aria-current=\{pageNumber === page \? "page"/);
});

test("bulk deletion preserves active jobs, sessions, and subtitle burns", () => {
  assert.match(historySource, /terminalStatuses\.has\(job\.status\)/);
  assert.match(liveSource, /session\.status !== "active" && session\.status !== "paused"/);
  assert.match(subtitleSource, /burn\?\.status !== "queued" && burn\?\.status !== "processing"/);
});

test("every History section has an animated first-load state", () => {
  for (const source of [historySource, liveSource, subtitleSource]) {
    assert.match(source, /<HistoryLoading label=/);
    assert.match(source, /!loading \? <HistoryPagination/);
  }
  assert.match(liveSource, /useState\(true\)/);
  assert.match(subtitleSource, /useState\(true\)/);
  assert.match(loadingSource, /role="status"/);
  assert.match(loadingSource, /history-loading-spinner/);
  assert.match(loadingSource, /history-loading-rows/);
});

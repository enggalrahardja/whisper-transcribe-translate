import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const pageSource = readFileSync(new URL("../app/settings/page.tsx", import.meta.url), "utf8");

test("Storage loads and renders the Local Files list", () => {
  assert.match(pageSource, /fetch\(`\$\{apiBaseUrl\}\/api\/settings\/local-files`/);
  assert.match(pageSource, /<h3>Local Files<\/h3>/);
  assert.match(pageSource, /className="local-files-table"/);
  assert.match(pageSource, /localFiles\.map\(\(file\)/);
});

test("Local Files exposes a guarded delete action", () => {
  assert.match(pageSource, /window\.confirm\(`Delete local file/);
  assert.match(pageSource, /method: "DELETE"/);
  assert.match(pageSource, /!file\.deletable/);
  assert.match(pageSource, /file\.protection_reason/);
});

test("Storage exposes an absolute storage location setting", () => {
  assert.match(pageSource, />Storage location<input/);
  assert.match(pageSource, /draft\.storage_retention\.storage_location/);
  assert.match(pageSource, /New uploads and exports use this location/);
  assert.match(pageSource, /Previous storage locations/);
});

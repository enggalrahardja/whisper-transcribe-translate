import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const configSource = readFileSync(new URL("../next.config.ts", import.meta.url), "utf8");

test("development and production builds use separate Next.js output directories", () => {
  assert.match(configSource, /NODE_ENV === "production" \? "\.next-production" : "\.next"/);
  assert.match(configSource, /distDir: process\.env\.NEXT_DIST_DIR \?\? defaultDistDir/);
});

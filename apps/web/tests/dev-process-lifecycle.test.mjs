import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const devSource = readFileSync(new URL("../../../scripts/dev.mjs", import.meta.url), "utf8");

test("dev startup clears every project service before checking ports", () => {
  assert.match(devSource, /app\\\.\(\?:worker\|model_downloader\)/);
  assert.match(devSource, /next\(\?:\\\/dist\\\/bin\\\/next\)\?\\s\+dev/);
  assert.ok(devSource.indexOf("await cleanupStaleProjectDevProcesses()") < devSource.indexOf("for (const port of ports)"));
});

test("dev shutdown waits for children and force-cleans survivors", () => {
  assert.match(devSource, /await waitForPidsToExit\(pids, gracefulShutdownMs\)/);
  assert.match(devSource, /stopChild\(child, true\)/);
  assert.match(devSource, /await waitForPidsToExit\(pids, forcedShutdownMs\)/);
});

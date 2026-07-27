import { spawn } from "node:child_process";
import path from "node:path";

import { productionEnv, projectRoot } from "./production-env.mjs";

const definitions = [
  ["web", "start-web.mjs"],
  ["api", "start-api.mjs"],
  ["worker", "start-worker.mjs"],
];
const env = productionEnv();
let stopping = false;
let requestedExitCode = 0;

const children = definitions.map(([name, script]) => {
  const child = spawn(process.execPath, [path.join(projectRoot, "scripts", script)], {
    cwd: projectRoot,
    env,
    stdio: "inherit",
    detached: process.platform !== "win32",
  });
  child.serviceName = name;
  return child;
});

function signalChild(child, signal) {
  if (!child.pid || child.exitCode !== null) return;
  try {
    // Signal the Node wrapper once; it forwards the signal to its service.
    // Sending to the whole process group here would also hit the service and
    // cause a duplicate SIGINT/SIGTERM during manual shutdown.
    if (signal === "SIGKILL" && process.platform !== "win32") process.kill(-child.pid, signal);
    else child.kill(signal);
  } catch (error) {
    if (error?.code !== "ESRCH") console.error(`Could not stop ${child.serviceName}: ${error.message}`);
  }
}

function shutdown(exitCode = 0, signal = "SIGTERM") {
  if (stopping) return;
  stopping = true;
  requestedExitCode = exitCode;
  for (const child of children) signalChild(child, signal);
  const forceTimer = setTimeout(() => {
    for (const child of children) signalChild(child, "SIGKILL");
  }, 10_000);
  forceTimer.unref();
}

for (const child of children) {
  child.on("error", (error) => {
    console.error(`${child.serviceName} failed to start: ${error.message}`);
    shutdown(1);
  });
  child.on("exit", (code, signal) => {
    if (!stopping) {
      console.error(`${child.serviceName} exited unexpectedly (${signal ?? `code ${code}`}).`);
      shutdown(code && code !== 0 ? code : 1);
    }
    if (children.every((item) => item.exitCode !== null || item.signalCode !== null)) {
      process.exit(requestedExitCode);
    }
  });
}

process.on("SIGINT", () => shutdown(0, "SIGINT"));
process.on("SIGTERM", () => shutdown(0, "SIGTERM"));

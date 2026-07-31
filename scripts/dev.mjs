import { execFileSync, spawn } from "node:child_process";
import { readlinkSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ports = [3000, 8000];
const gracefulShutdownMs = 5000;
const forcedShutdownMs = 3000;
const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const projectDevCommand = /(?:scripts\/dev(?:-api|-worker|-model-downloader)?\.mjs|pnpm(?:\.cjs)?\s+(?:run\s+)?dev(?::(?:web|api|worker|model-downloader))?|next(?:\/dist\/bin\/next)?\s+dev|uvicorn\s+app\.main:app\s+--reload|-m\s+app\.(?:worker|model_downloader))/;

function findPidsOnWindows(port) {
  try {
    const output = execFileSync("netstat", ["-ano", "-p", "tcp"], { encoding: "utf8" });
    const pids = new Set();

    for (const line of output.split(/\r?\n/)) {
      const parts = line.trim().split(/\s+/);
      if (parts.length < 5 || parts[0] !== "TCP") continue;

      const localAddress = parts[1];
      const state = parts[3];
      const pid = parts[4];

      if (state === "LISTENING" && localAddress.endsWith(`:${port}`) && /^\d+$/.test(pid)) {
        pids.add(pid);
      }
    }

    return [...pids];
  } catch {
    return [];
  }
}

function findPidsOnUnix(port) {
  const pids = new Set();

  try {
    const output = execFileSync("lsof", ["-nP", `-iTCP:${port}`, "-sTCP:LISTEN", "-t"], { encoding: "utf8" });
    for (const value of output.split(/\s+/)) {
      if (/^\d+$/.test(value)) pids.add(value);
    }
  } catch {
    // lsof may not expose listeners in some Linux environments.
  }

  try {
    const output = execFileSync("ss", ["-H", "-ltnp", `sport = :${port}`], { encoding: "utf8" });
    for (const match of output.matchAll(/pid=(\d+)/g)) pids.add(match[1]);
  } catch {
    // ss is Linux-specific; lsof remains the portable Unix fallback.
  }

  return [...pids];
}

function findPids(port) {
  return process.platform === "win32" ? findPidsOnWindows(port) : findPidsOnUnix(port);
}

function getProcessCommand(pid) {
  try {
    if (process.platform === "win32") {
      return execFileSync(
        "powershell",
        ["-NoProfile", "-Command", `(Get-CimInstance Win32_Process -Filter \"ProcessId = ${pid}\").CommandLine`],
        { encoding: "utf8" },
      ).trim();
    }
    return execFileSync("ps", ["-p", pid, "-o", "command="], { encoding: "utf8" }).trim();
  } catch {
    return "";
  }
}

function getProcessWorkingDirectory(pid) {
  if (process.platform === "win32") return "";

  try {
    return readlinkSync(`/proc/${pid}/cwd`);
  } catch {
    try {
      const output = execFileSync("lsof", ["-a", "-p", pid, "-d", "cwd", "-Fn"], { encoding: "utf8" });
      return output.split(/\r?\n/).find((line) => line.startsWith("n"))?.slice(1) ?? "";
    } catch {
      return "";
    }
  }
}

function isProjectProcess(pid) {
  const workingDirectory = getProcessWorkingDirectory(pid);
  const command = getProcessCommand(pid);
  const isWithinProject = workingDirectory === projectRoot || workingDirectory.startsWith(`${projectRoot}${path.sep}`);
  return isWithinProject || command.includes(projectRoot);
}

function terminatePid(pid, force = false) {
  try {
    if (process.platform === "win32") {
      execFileSync("taskkill", ["/PID", pid, "/T", "/F"], { stdio: "ignore" });
    } else {
      process.kill(Number(pid), force ? "SIGKILL" : "SIGTERM");
    }
    console.log(`${force ? "Force-terminated" : "Terminated"} PID ${pid}`);
  } catch {
    // Process may already have stopped.
  }
}

function listProcesses() {
  if (process.platform === "win32") {
    try {
      const output = execFileSync("powershell", [
        "-NoProfile",
        "-Command",
        "Get-CimInstance Win32_Process | ForEach-Object { \"$($_.ProcessId)`t$($_.ParentProcessId)`t$($_.CommandLine)\" }",
      ], { encoding: "utf8" });
      return output.split(/\r?\n/).map((line) => {
        const [pid, ppid, ...command] = line.split("\t");
        return { pid: Number(pid), ppid: Number(ppid), command: command.join("\t") };
      }).filter((item) => Number.isInteger(item.pid) && item.pid > 0);
    } catch {
      return [];
    }
  }
  try {
    const output = execFileSync("ps", ["-eo", "pid=,ppid=,command="], { encoding: "utf8" });
    return output.split(/\r?\n/).map((line) => {
      const match = line.trim().match(/^(\d+)\s+(\d+)\s+(.*)$/);
      return match ? { pid: Number(match[1]), ppid: Number(match[2]), command: match[3] } : null;
    }).filter(Boolean);
  } catch {
    return [];
  }
}

function currentProcessFamily(processes) {
  const byPid = new Map(processes.map((item) => [item.pid, item]));
  const protectedPids = new Set([process.pid]);
  let current = byPid.get(process.pid);
  while (current?.ppid && !protectedPids.has(current.ppid)) {
    protectedPids.add(current.ppid);
    current = byPid.get(current.ppid);
  }
  return protectedPids;
}

function processExists(pid) {
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}

async function waitForPidsToExit(pids, timeoutMs) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (pids.every((pid) => !processExists(pid))) return true;
    await delay(150);
  }
  return pids.every((pid) => !processExists(pid));
}

async function cleanupStaleProjectDevProcesses() {
  const processes = listProcesses();
  const protectedPids = currentProcessFamily(processes);
  const stalePids = processes
    .filter((item) => !protectedPids.has(item.pid))
    .filter((item) => projectDevCommand.test(item.command) && isProjectProcess(String(item.pid)))
    .map((item) => item.pid);
  if (stalePids.length === 0) {
    console.log("No stale project development processes found.");
    return;
  }
  console.log(`Stopping ${stalePids.length} stale project development process(es).`);
  for (const pid of stalePids) terminatePid(String(pid));
  if (!(await waitForPidsToExit(stalePids, gracefulShutdownMs))) {
    for (const pid of stalePids.filter(processExists)) terminatePid(String(pid), true);
  }
  if (!(await waitForPidsToExit(stalePids, forcedShutdownMs))) {
    console.error(`Development PID(s) could not be stopped: ${stalePids.filter(processExists).join(", ")}`);
    process.exit(1);
  }
}

function delay(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

async function waitForPortToBeFree(port, timeoutMs) {
  const deadline = Date.now() + timeoutMs;

  while (Date.now() < deadline) {
    if (findPids(port).length === 0) return true;
    await delay(150);
  }

  return findPids(port).length === 0;
}

await cleanupStaleProjectDevProcesses();

for (const port of ports) {
  const existingPids = findPids(port);
  const unrelatedPids = existingPids.filter((pid) => !isProjectProcess(pid));

  if (unrelatedPids.length > 0) {
    console.error(`Port ${port} is used by non-project PID(s): ${unrelatedPids.join(", ")}. Nothing was terminated.`);
    process.exit(1);
  }

  if (existingPids.length > 0) {
    console.log(`Port ${port} is in use; stopping ${existingPids.length} listening process(es).`);
    for (const pid of existingPids) terminatePid(pid);
  }

  if (!(await waitForPortToBeFree(port, gracefulShutdownMs))) {
    for (const pid of findPids(port)) terminatePid(pid, true);
  }

  if (!(await waitForPortToBeFree(port, forcedShutdownMs))) {
    console.error(`Port ${port} could not be released.`);
    process.exit(1);
  }

  console.log(`Port ${port} is available.`);
}

const commands = [
  { name: "web", command: "pnpm", args: ["run", "dev:web"] },
  { name: "api", command: "pnpm", args: ["run", "dev:api"] },
  { name: "worker", command: "pnpm", args: ["run", "dev:worker"] },
  { name: "whisper-model-downloader", command: "pnpm", args: ["run", "dev:model-downloader"] },
];

const children = commands.map(({ name, command, args }) => {
  const child = spawn(command, args, {
    stdio: "inherit",
    shell: process.platform === "win32",
    detached: process.platform !== "win32",
  });

  child.on("error", (error) => {
    console.error(`${name} gagal dijalankan: ${error.message}`);
    shutdown(1);
  });

  child.on("exit", (code) => {
    if (!stopping) {
      const exitCode = code && code !== 0 ? code : 1;
      console.error(`${name} berhenti tak terduga dengan exit code ${code ?? "unknown"}`);
      shutdown(exitCode);
    }
  });

  return child;
});

let stopping = false;
let shutdownPromise = null;

function stopChild(child, force = false) {
  if (!child.pid) return;

  try {
    if (process.platform === "win32") {
      execFileSync("taskkill", ["/PID", String(child.pid), "/T", "/F"], { stdio: "ignore" });
    } else {
      process.kill(-child.pid, force ? "SIGKILL" : "SIGTERM");
    }
  } catch {
    try {
      child.kill(force ? "SIGKILL" : "SIGTERM");
    } catch {
      // Already stopped.
    }
  }
}

function shutdown(code = 0) {
  if (shutdownPromise) return shutdownPromise;
  stopping = true;
  shutdownPromise = (async () => {
    const running = children.filter((child) => child.exitCode === null && child.signalCode === null);
    for (const child of running) stopChild(child);
    const pids = running.map((child) => child.pid).filter(Boolean);
    if (!(await waitForPidsToExit(pids, gracefulShutdownMs))) {
      for (const child of running.filter((item) => item.pid && processExists(item.pid))) stopChild(child, true);
      await waitForPidsToExit(pids, forcedShutdownMs);
    }
    process.exit(code);
  })();
  return shutdownPromise;
}

process.on("SIGINT", () => void shutdown(0));
process.on("SIGTERM", () => void shutdown(0));
process.on("exit", () => {
  if (!stopping) {
    stopping = true;
    for (const child of children) stopChild(child, true);
  }
});

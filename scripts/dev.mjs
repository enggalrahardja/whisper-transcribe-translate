import { execFileSync, spawn } from "node:child_process";
import { readlinkSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const ports = [3000, 8000];
const gracefulShutdownMs = 5000;
const forcedShutdownMs = 3000;
const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

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

function stopChild(child) {
  if (!child.pid) return;

  try {
    if (process.platform === "win32") {
      execFileSync("taskkill", ["/PID", String(child.pid), "/T", "/F"], { stdio: "ignore" });
    } else {
      process.kill(-child.pid, "SIGTERM");
    }
  } catch {
    try {
      child.kill("SIGTERM");
    } catch {
      // Already stopped.
    }
  }
}

function shutdown(code = 0) {
  if (stopping) return;
  stopping = true;
  for (const child of children) stopChild(child);
  process.exit(code);
}

process.on("SIGINT", () => shutdown(0));
process.on("SIGTERM", () => shutdown(0));
process.on("exit", () => {
  if (!stopping) {
    stopping = true;
    for (const child of children) stopChild(child);
  }
});

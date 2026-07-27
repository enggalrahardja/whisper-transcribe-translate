import { execFileSync, spawn } from "node:child_process";

const ports = [3000, 8000];

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
  try {
    const output = execFileSync("lsof", ["-ti", `tcp:${port}`], { encoding: "utf8" });
    return [...new Set(output.split(/\s+/).filter((value) => /^\d+$/.test(value)))];
  } catch {
    return [];
  }
}

function terminatePid(pid) {
  try {
    if (process.platform === "win32") {
      execFileSync("taskkill", ["/PID", pid, "/T", "/F"], { stdio: "ignore" });
    } else {
      process.kill(Number(pid), "SIGTERM");
    }
    console.log(`Terminated PID ${pid}`);
  } catch {
    // Process may already have stopped.
  }
}

for (const port of ports) {
  const pids = process.platform === "win32" ? findPidsOnWindows(port) : findPidsOnUnix(port);
  for (const pid of pids) terminatePid(pid);
}

const commands = [
  { name: "web", command: "pnpm", args: ["run", "dev:web"] },
  { name: "api", command: "pnpm", args: ["run", "dev:api"] },
];

const children = commands.map(({ name, command, args }) => {
  const child = spawn(command, args, {
    stdio: "inherit",
    shell: process.platform === "win32",
  });

  child.on("error", (error) => {
    console.error(`${name} gagal dijalankan: ${error.message}`);
    shutdown(1);
  });

  child.on("exit", (code) => {
    if (!stopping && code && code !== 0) {
      console.error(`${name} berhenti dengan exit code ${code}`);
      shutdown(code);
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

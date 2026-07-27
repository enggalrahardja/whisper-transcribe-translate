import { existsSync } from "node:fs";
import { spawn } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const apiDir = path.join(root, "services", "api");
const candidates = process.platform === "win32"
  ? [path.join(apiDir, ".venv", "Scripts", "python.exe"), "python"]
  : [path.join(apiDir, ".venv", "bin", "python"), "python3", "python"];
const python = candidates.find((candidate) => candidate.includes(path.sep) ? existsSync(candidate) : true);

if (!python) {
  console.error("Python executable untuk Whisper model downloader tidak ditemukan.");
  process.exit(1);
}

const child = spawn(python, ["-m", "app.model_downloader"], {
  cwd: apiDir,
  stdio: "inherit",
  shell: false,
});

child.on("error", (error) => {
  console.error(`Whisper model downloader gagal dijalankan: ${error.message}`);
  process.exit(1);
});

function stop(signal) {
  if (!child.killed) child.kill(signal);
}

process.on("SIGINT", () => stop("SIGINT"));
process.on("SIGTERM", () => stop("SIGTERM"));
child.on("exit", (code) => process.exit(code ?? 0));

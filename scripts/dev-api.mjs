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
  console.error("Python executable tidak ditemukan.");
  process.exit(1);
}

const child = spawn(
  python,
  ["-m", "uvicorn", "app.main:app", "--reload", "--host", "127.0.0.1", "--port", "8000"],
  {
    cwd: apiDir,
    stdio: "inherit",
    shell: false,
  },
);

child.on("error", (error) => {
  console.error(`FastAPI gagal dijalankan: ${error.message}`);
  process.exit(1);
});

child.on("exit", (code) => process.exit(code ?? 0));

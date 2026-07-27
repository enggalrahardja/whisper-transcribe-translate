import { existsSync } from "node:fs";
import { spawn } from "node:child_process";
import path from "node:path";

import { productionEnv, projectPython, projectRoot } from "./production-env.mjs";
import { runChild } from "./run-child.mjs";

const python = projectPython();
if (!existsSync(python)) {
  console.error(`Project Python virtualenv was not found: ${python}`);
  process.exit(1);
}

const env = productionEnv();
const workers = Number.parseInt(env.API_WORKERS, 10);
if (!Number.isInteger(workers) || workers < 1 || workers > 4) {
  console.error("API_WORKERS must be an integer between 1 and 4.");
  process.exit(1);
}

const child = spawn(
  python,
  ["-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000", "--workers", String(workers)],
  {
    cwd: path.join(projectRoot, "services", "api"),
    env,
    stdio: "inherit",
  },
);

runChild(child, "FastAPI production server");

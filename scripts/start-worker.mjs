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

const child = spawn(python, ["-m", "app.worker"], {
  cwd: path.join(projectRoot, "services", "api"),
  env: productionEnv(),
  stdio: "inherit",
});

runChild(child, "transcription worker");

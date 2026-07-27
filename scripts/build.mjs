import { spawn } from "node:child_process";

import { productionEnv, projectRoot } from "./production-env.mjs";
import { runChild } from "./run-child.mjs";

const command = process.platform === "win32" ? "pnpm.cmd" : "pnpm";
const child = spawn(command, ["--dir", "apps/web", "build"], {
  cwd: projectRoot,
  env: productionEnv(),
  stdio: "inherit",
});

runChild(child, "Next.js production build");

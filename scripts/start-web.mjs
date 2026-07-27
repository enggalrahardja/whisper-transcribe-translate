import { spawn } from "node:child_process";
import path from "node:path";

import { productionEnv, projectRoot } from "./production-env.mjs";
import { runChild } from "./run-child.mjs";

const env = productionEnv();
const webDir = path.join(projectRoot, "apps", "web");
const nextBin = path.join(webDir, "node_modules", "next", "dist", "bin", "next");
const child = spawn(process.execPath, [nextBin, "start", "--hostname", "127.0.0.1", "--port", "3000"], {
  cwd: webDir,
  env,
  stdio: "inherit",
});

runChild(child, "Next.js production server");

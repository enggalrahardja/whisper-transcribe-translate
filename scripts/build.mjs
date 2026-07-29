import { spawn } from "node:child_process";

import { productionEnv, projectRoot } from "./production-env.mjs";
import { runChild } from "./run-child.mjs";

const pnpmArgs = ["--dir", "apps/web", "build"];
const pnpmEntryPoint = process.env.npm_execpath;
const command = pnpmEntryPoint
  ? process.execPath
  : process.platform === "win32"
    ? process.env.ComSpec ?? "cmd.exe"
    : "pnpm";
const args = pnpmEntryPoint
  ? [pnpmEntryPoint, ...pnpmArgs]
  : process.platform === "win32"
    ? ["/d", "/s", "/c", "pnpm", ...pnpmArgs]
    : pnpmArgs;

const child = spawn(command, args, {
  cwd: projectRoot,
  env: productionEnv(),
  stdio: "inherit",
});

runChild(child, "Next.js production build");

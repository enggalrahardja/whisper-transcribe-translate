import { readFileSync } from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

export const projectRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

function parseEnvFile(contents) {
  const values = {};
  for (const rawLine of contents.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith("#")) continue;
    const match = line.match(/^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$/);
    if (!match) throw new Error(`Invalid production environment line: ${rawLine}`);
    let value = match[2].trim();
    if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
      value = value.slice(1, -1);
    } else {
      value = value.replace(/\s+#.*$/, "");
    }
    values[match[1]] = value;
  }
  return values;
}

export function productionEnv() {
  let fileValues = {};
  try {
    fileValues = parseEnvFile(readFileSync(path.join(projectRoot, ".env.production"), "utf8"));
  } catch (error) {
    if (error?.code !== "ENOENT") throw error;
  }

  return {
    APP_ENV: "production",
    API_HOST: "127.0.0.1",
    API_PORT: "8000",
    API_WORKERS: "1",
    WEB_HOST: "127.0.0.1",
    WEB_PORT: "3000",
    WEB_ORIGIN: "http://127.0.0.1:3000",
    NEXT_PUBLIC_API_BASE_URL: "http://127.0.0.1:8000",
    NEXT_DIST_DIR: ".next-production",
    NODE_ENV: "production",
    ...fileValues,
    ...process.env,
    APP_ENV: "production",
    NODE_ENV: "production",
  };
}

export function projectPython() {
  const relative = process.platform === "win32"
    ? path.join("services", "api", ".venv", "Scripts", "python.exe")
    : path.join("services", "api", ".venv", "bin", "python");
  return path.join(projectRoot, relative);
}

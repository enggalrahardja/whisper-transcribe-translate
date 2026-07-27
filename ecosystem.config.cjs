const fs = require("node:fs");
const path = require("node:path");

const root = __dirname;
const apiDir = path.join(root, "services", "api");
const python = path.join(apiDir, ".venv", "bin", "python");

function productionEnv() {
  const values = {};
  const envPath = path.join(root, ".env.production");
  if (fs.existsSync(envPath)) {
    for (const rawLine of fs.readFileSync(envPath, "utf8").split(/\r?\n/)) {
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
  }
  return {
    APP_ENV: "production",
    API_HOST: "127.0.0.1",
    API_PORT: "8000",
    WEB_ORIGIN: "http://127.0.0.1:3000",
    NEXT_PUBLIC_API_BASE_URL: "http://127.0.0.1:8000",
    NODE_ENV: "production",
    ...values,
    APP_ENV: "production",
    NODE_ENV: "production",
  };
}

const env = productionEnv();

module.exports = {
  apps: [
    {
      name: "whisper-web",
      cwd: path.join(root, "apps", "web"),
      script: path.join(root, "apps", "web", "node_modules", "next", "dist", "bin", "next"),
      args: "start --hostname 127.0.0.1 --port 3000",
      interpreter: process.execPath,
      exec_mode: "fork",
      instances: 1,
      env,
      kill_timeout: 10000,
    },
    {
      name: "whisper-api",
      cwd: apiDir,
      script: python,
      args: "-m uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 1",
      interpreter: "none",
      exec_mode: "fork",
      instances: 1,
      env,
      kill_timeout: 10000,
    },
    {
      name: "whisper-worker",
      cwd: apiDir,
      script: python,
      args: "-m app.worker",
      interpreter: "none",
      exec_mode: "fork",
      instances: 1,
      env,
      kill_timeout: 15000,
    },
  ],
};

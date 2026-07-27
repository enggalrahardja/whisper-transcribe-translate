import { productionEnv } from "./production-env.mjs";

const env = productionEnv();
const checks = [
  ["web", `http://127.0.0.1:3000/`, null],
  ["api", `http://127.0.0.1:8000/health`, (body) => body.status === "ok" && body.service === "api"],
  ["mongodb", `http://127.0.0.1:8000/health/mongodb`, (body) => body.status === "ok"],
  ["worker", `http://127.0.0.1:8000/health/worker`, (body) => body.status === "ok" && body.last_heartbeat],
];

async function check(name, url, validate) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), Number(env.HEALTH_TIMEOUT_MS || 5000));
  try {
    const response = await fetch(url, { signal: controller.signal });
    if (response.status !== 200) throw new Error(`HTTP ${response.status}`);
    if (validate) {
      const body = await response.json();
      if (!validate(body)) throw new Error("unexpected response body");
    }
    console.log(`ok  ${name}  ${url}`);
    return true;
  } catch (error) {
    console.error(`FAIL  ${name}  ${url}  ${error.message}`);
    return false;
  } finally {
    clearTimeout(timer);
  }
}

const results = await Promise.all(checks.map((item) => check(...item)));
if (results.some((result) => !result)) process.exit(1);

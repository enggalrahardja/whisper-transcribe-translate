import assert from "node:assert/strict";
import test from "node:test";

import { contentSecurityPolicy, securityHeaders } from "../security-headers.mjs";

const existingProductionPolicy = "default-src 'self'; connect-src 'self' http: https: ws: wss:; media-src 'self' blob:; worker-src 'self' blob:; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; frame-ancestors 'none'; base-uri 'self'; form-action 'self'";

test("development CSP permits the Next.js development runtime", () => {
  const policy = contentSecurityPolicy("development");

  assert.match(policy, /script-src 'self' 'unsafe-inline' 'unsafe-eval'/);
  assert.match(policy, /connect-src 'self' http: https: ws: wss:/);
});

test("production CSP remains strict and unchanged", () => {
  const policy = contentSecurityPolicy("production");

  assert.equal(policy, existingProductionPolicy);
  assert.doesNotMatch(policy, /unsafe-eval/);
  assert.doesNotMatch(policy, /script-src[^;]*unsafe-inline/);
});

test("security headers contain exactly one CSP header", () => {
  for (const environment of ["development", "production"]) {
    const cspHeaders = securityHeaders(environment).filter(
      ({ key }) => key.toLowerCase() === "content-security-policy",
    );

    assert.equal(cspHeaders.length, 1, environment);
    assert.equal(cspHeaders[0].value, contentSecurityPolicy(environment));
  }
});

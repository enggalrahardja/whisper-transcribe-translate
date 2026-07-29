const productionContentSecurityPolicy = "default-src 'self'; connect-src 'self' http: https: ws: wss:; media-src 'self' blob:; worker-src 'self' blob:; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; frame-ancestors 'none'; base-uri 'self'; form-action 'self'";

export function contentSecurityPolicy(environment = process.env.NODE_ENV) {
  if (environment !== "development") return productionContentSecurityPolicy;

  return productionContentSecurityPolicy.replace(
    "default-src 'self';",
    "default-src 'self'; script-src 'self' 'unsafe-inline' 'unsafe-eval';",
  );
}

export function securityHeaders(environment = process.env.NODE_ENV) {
  return [
    { key: "X-Content-Type-Options", value: "nosniff" },
    { key: "X-Frame-Options", value: "DENY" },
    { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
    { key: "Permissions-Policy", value: "camera=(self), microphone=(self), geolocation=()" },
    { key: "Content-Security-Policy", value: contentSecurityPolicy(environment) },
  ];
}

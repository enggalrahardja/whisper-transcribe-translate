# Stage 18 security checklist

| Control | Status | Evidence |
|---|---|---|
| Authentication/authorization/session ownership | **PASS** | Stage 16 regression tests |
| WebSocket auth/origin/input/rate limits | **PASS** | Stage 16 regression tests |
| Monitoring content/secret redaction | **PASS** | monitoring and persistence redaction tests |
| Local mode makes no cloud request | **PASS** | local defaults/provider-selection tests |
| OpenAI requires explicit key and consent | **PASS** | provider/profile startup tests |
| Deployed TLS/origin/secret manager | **PENDING** | Automated auth/redaction tests pass; deployed TLS/origin/secret-manager acceptance was not executed |

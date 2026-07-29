# Stage 16 — production hardening

## Production deployment checklist

1. Copy `.env.production.example` to `.env.production`, generate random bearer
   tokens, and remove the rejected placeholder.
2. Keep `APP_DEBUG=false`, `SECURITY_AUTH_ENABLED=true`, and select only
   `Fast`, `Balanced`, `Accurate`, or `Private`.
3. Install the exact required local Whisper checkpoint before startup. Stage 16
   does not download a missing production model or add a cloud provider.
4. Set an explicit HTTPS origin with no wildcard. Terminate TLS at the reverse
   proxy, configure Uvicorn proxy-header trust only for that proxy, and expose
   WebSocket as WSS.
5. Restrict MongoDB and storage permissions to the service identity, configure
   encrypted volumes/database encryption, backups, restore drills, and retention.
6. Verify `/health`, `/health/readiness`, authentication, origin rejection,
   resource limits, cleanup dry-run, and audit ingestion before traffic.

Development remains easy to run: authentication and HTTPS enforcement default
off, localhost origins are allowed, pipeline feature flags stay off, and the
legacy local `base` path is unchanged.

## Authentication and authorization

`SECURITY_TOKENS_JSON` maps high-entropy bearer tokens to `userId` and `role`
(`user` or `admin`). Tokens come only from environment configuration and are
compared with constant-time equality. HTTP clients send `Authorization: Bearer
<token>`. Browser WebSockets may use the same header where supported or the
`bearer,<token>` WebSocket subprotocol (preferred) or the compatibility
`access_token` query parameter. Query tokens can appear in proxy access logs, so
disable/redact URI logging when that fallback is used. WSS is mandatory when
authentication is active.

Every live session stores `owner_id`. Owners can create, list, restore, read,
stop, and rename speakers only in their own sessions. Admins may access all
sessions, system monitoring, glossary reload, settings/model operations, and
retention cleanup. Session-level monitoring is available to its owner; system
monitoring is admin-only. WebSocket authentication and ownership checks occur
before `accept()`.

## Rate and resource limits

| Environment variable | Default | Meaning |
|---|---:|---|
| `RATE_SESSION_CREATE_PER_MINUTE` | 10 | New sessions per principal/minute |
| `RATE_WEBSOCKET_CONNECT_PER_MINUTE` | 20 | Connections per principal/minute |
| `RATE_AUDIO_BYTES_PER_SECOND` | 128000 | Aggregate live audio throughput/principal |
| `RATE_GLOSSARY_RELOAD_PER_MINUTE` | 2 | Admin reloads/minute |
| `RATE_MONITORING_PER_MINUTE` | 30 | Monitoring reads/minute |
| `LIMIT_CONCURRENT_SESSIONS` | 8 | Active/paused sessions per owner |
| `LIMIT_SESSION_DURATION_SECONDS` | 14400 | Maximum live session duration |
| `LIMIT_AUDIO_CHUNK_BYTES` | 524288 | Maximum WebSocket binary frame |
| `LIMIT_QUEUE_DEPTH` | 256 | Maximum accepted configured worker capacity |
| `LIMIT_UPLOAD_BYTES` | 1073741824 | Hard upload ceiling in addition to existing setting |
| `LIMIT_RECONNECT_ATTEMPTS` | 20 | Process-lifetime reconnect ceiling/session |
| `WEBSOCKET_HEARTBEAT_SECONDS` | 10 | Server heartbeat interval |
| `WEBSOCKET_IDLE_TIMEOUT_SECONDS` | 30 | Disconnect after no client activity |

Limiters are bounded in-process sliding windows. Queue overflow continues using
the existing explicit backpressure contracts. Multi-instance deployments need
a shared limiter before claiming global rate guarantees.

PCM metadata accepts only the exact contract fields, exact integer types for
sequence/rate/channel/length, finite numeric timestamps/durations, safe session
IDs, PCM16 mono 16 kHz, 100–250 ms, even and exact byte length, and a matching
binary frame. Empty, malformed, oversized, unsupported, and excessive-throughput
frames are rejected before ingestion/transcription.

## Audit events and redaction

The `security_audit_events` collection records schema version, event, outcome,
actor ID/role, optional session ID, safe metadata, and timestamp. Events include
session start/stop, auth failure, glossary reload, speaker rename, profile/model
setting change, monitoring access, rate rejection, and retention cleanup.

Audit metadata rejects transcript, translation, audio content/bytes, secrets,
tokens, passwords, local paths, and checkpoint paths. Public server errors use
a stable message in production; health failures never include connection
strings, local paths, stack traces, worker IDs, or internal configuration.

## Retention and cleanup

Separate day-based settings cover session metadata, audio files, transcript,
translation, metrics, and audit events. `RETENTION_CLEANUP_BATCH_SIZE` bounds one
run. `POST /api/operations/retention/cleanup?dry_run=true` is admin-only and
defaults to dry-run. Applying cleanup removes eligible database records and
regular non-symlink files directly under `storage/uploads`; path containment is
checked. The queue remains in-process and is not made durable by retention.

Backup policy must be coordinated with retention: encrypt backups, restrict
restore operators, test point-in-time MongoDB recovery and storage/reference
consistency, and ensure expired content also ages out of backups according to
the organization’s policy.

## TLS, secrets, and encryption

The API is ready for HTTPS/WSS behind a trusted reverse proxy and can require
HTTPS via `SECURITY_REQUIRE_HTTPS`. API and web responses set content-type,
frame, referrer, permissions, and CSP headers; HSTS is sent when HTTPS is
required. Credentials are environment-only and intentionally absent from source.

Application-level encryption at rest is not implemented. MongoDB encryption,
filesystem/disk encryption, key rotation, certificate renewal, and backup
encryption remain deployment responsibilities.

## Health and readiness

- `/health` is liveness and exposes only service/environment status.
- `/health/readiness` checks MongoDB, writable storage, local `base.pt`, live
  worker supervisor, queue capacity, and persistence degradation. It returns
  HTTP 503 when a mandatory dependency is unavailable.
- `/health/mongodb` and `/health/worker` return sanitized component status.

Startup rejects non-positive limits, invalid retention, queue capacities above
the production ceiling, heartbeat/idle inconsistencies, unsupported profiles,
debug mode, disabled production auth, placeholder/short secrets, wildcard or
non-HTTPS trusted origins when required, and a missing required local checkpoint.

## Known limitations

- Bearer token mapping is deployment-managed static authentication, not OAuth,
  OIDC, password login, refresh tokens, or centralized revocation.
- Rate/reconnect state is per process and resets on restart.
- Audit writes currently depend on MongoDB; production log shipping/tamper-proof
  storage is not included.
- Cleanup is manually invoked; no distributed scheduler or legal-hold workflow.
- TLS termination and encryption at rest are external deployment controls.
- Legacy upload and local processing behavior remains compatible, including
  existing provider choices; Stage 16 adds no cloud provider.

## Security test checklist

- Valid/invalid bearer token and user/admin authorization.
- Cross-owner session read, restore, stop, and rename rejection.
- WebSocket token and trusted-origin rejection before accept.
- Creation, connection, throughput, glossary, and monitoring throttles.
- Concurrent session, duration, reconnect, upload, chunk, and queue bounds.
- Malformed PCM types, fields, length, rate, channels, and frame mismatch.
- Heartbeat, idle timeout, graceful disconnect, and reconnect behavior.
- Public error, audit record, health, and monitoring redaction.
- Retention dry-run, batch bound, path containment, and applied cleanup.
- Unsafe production configurations and missing dependencies return failure.
- Development legacy/local defaults and all pipeline regression tests.

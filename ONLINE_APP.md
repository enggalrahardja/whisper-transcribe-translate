# Whisper Transcribe & Translate — Online App

The repository contains the original desktop application plus a local online workspace:

```text
apps/web/              Next.js dashboard and editors
services/api/          FastAPI API and transcription worker
services/api/storage/  Runtime uploads and generated exports (ignored by Git)
```

The root `pnpm run dev` command starts the web app, API, and worker without Docker. It is intended for a trusted local machine or a protected private network. The API currently has no user authentication or tenant isolation; do not expose port 8000 directly to the public internet without adding an authentication/reverse-proxy boundary.

## Ubuntu requirements

- Ubuntu 22.04 or newer
- Node.js 20+ and Corepack/pnpm 10
- Python 3.10+
- MongoDB 6+ running as a native service
- FFmpeg and FFprobe
- Enough disk space for Whisper model downloads and media storage
- Optional NVIDIA driver/CUDA-compatible PyTorch for GPU processing

Verify the required commands:

```bash
node --version
pnpm --version
python3 --version
mongosh --eval 'db.runCommand({ ping: 1 })'
ffmpeg -version
ffprobe -version
```

Install the common Ubuntu packages (MongoDB itself should be installed from the official MongoDB repository for your Ubuntu release):

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip ffmpeg curl
sudo systemctl enable --now mongod
```

## Installation

From the repository root:

```bash
corepack enable
corepack prepare pnpm@10.14.0 --activate
pnpm install

python3 -m venv services/api/.venv
services/api/.venv/bin/pip install --upgrade pip
services/api/.venv/bin/pip install -r services/api/requirements.txt
```

Create local environment files without committing them:

```bash
cp services/api/.env.example services/api/.env
cp apps/web/.env.example apps/web/.env.local
```

## Environment variables

`services/api/.env`:

| Variable | Default | Purpose |
| --- | --- | --- |
| `APP_NAME` | `Whisper Transcribe & Translate API` | API display name |
| `APP_ENV` | `development` | Enables localhost development origins |
| `WEB_ORIGIN` | `http://localhost:3000` | Allowed browser origin |
| `MONGODB_URI` | `mongodb://127.0.0.1:27017` | MongoDB connection URI; credentials stay server-side |
| `MONGODB_DATABASE` | `whisper_transcribe_translate` | Database name |
| `STORAGE_ROOT` | `storage` | Storage path relative to `services/api` |
| `WORKER_POLL_INTERVAL_SECONDS` | `1` | Initial worker polling fallback |
| `WORKER_HEARTBEAT_INTERVAL_SECONDS` | `5` | Worker heartbeat interval |
| `WORKER_STALE_AFTER_SECONDS` | `60` | Initial stale-job recovery threshold |

`apps/web/.env.local`:

| Variable | Default | Purpose |
| --- | --- | --- |
| `NEXT_PUBLIC_API_BASE_URL` | `http://127.0.0.1:8000` | Browser-visible API base URL |

Provider credentials, if a future translation provider requires them, must remain in `services/api/.env`. They must not be added to application settings or any `NEXT_PUBLIC_*` variable.

Most runtime values are managed on `/settings` and stored in the single versioned `application_settings` MongoDB document. Device and worker concurrency changes are marked restart-required; safe values are refreshed through a short worker cache.

## Running the application

Make sure MongoDB is reachable, then run from the repository root:

```bash
pnpm run dev
```

Services:

- Web: `http://127.0.0.1:3000`
- API: `http://127.0.0.1:8000`
- API health: `http://127.0.0.1:8000/health`
- MongoDB health: `http://127.0.0.1:8000/health/mongodb`

The root command checks ports 3000 and 8000, terminates only listeners belonging to this project, and stops web/API/worker process groups on Ctrl+C. It does not kill unrelated Python or Node processes.

## Storage structure

Paths are resolved below `services/api/storage`; database paths outside this boundary are rejected.

```text
services/api/storage/
├── uploads/   Randomly named validated audio/video uploads
└── exports/   FFmpeg subtitle-burn outputs
```

Uploads are limited by configured size, extension, and a matching container signature. Original names are metadata only and are normalized before use. Retention cleanup protects queued/processing jobs and media referenced by subtitle projects.

Do not commit `storage`, `.env`, `.venv`, `.next`, `node_modules`, Python caches, browser profiles, or generated media.

## Production build and checks

```bash
git diff --check
services/api/.venv/bin/python -m compileall -q services/api/app
pnpm --dir apps/web exec tsc --noEmit
pnpm --dir apps/web run build
```

The development server binds to loopback. A public deployment needs, at minimum, HTTPS/WSS, authentication and authorization, rate limiting, upload malware scanning, per-user ownership checks, and a reverse proxy that restricts API and download access.

## Troubleshooting

### Port 3000 or 8000 is occupied

`pnpm run dev` removes stale listeners only when their working directory or command belongs to this project. If it reports a non-project PID, stop that process yourself or change its configuration; the dev command deliberately leaves it untouched.

### MongoDB unavailable

Check the service and URI:

```bash
sudo systemctl status mongod
mongosh "$MONGODB_URI" --eval 'db.runCommand({ ping: 1 })'
```

API/worker startup fails fast when indexes or the active settings document cannot be reached.

### FFmpeg unavailable or subtitle burn fails

```bash
command -v ffmpeg
ffmpeg -version
```

Burn requests run in the background. Missing FFmpeg, invalid video, filter errors, timeouts, or read-only export storage produce a terminal `failed` burn record instead of blocking the HTTP request.

### Translation provider unavailable

Translation uses bounded chunk lengths, timeout handling, and configured retries. Provider/network failure marks the job `failed`; use History to retry after connectivity returns.

### Whisper model unavailable

The first use downloads the selected model into the user's Whisper cache. Check network connectivity, cache permissions, free disk space, and CUDA/PyTorch compatibility. Model-loading errors mark the claimed job `failed`.

### Storage unavailable or read-only

Verify ownership and free space:

```bash
mkdir -p services/api/storage/uploads services/api/storage/exports
test -w services/api/storage && echo writable
df -h services/api/storage
```

An upload is not registered as a media/job record unless the file is written and validated successfully.

### Worker appears offline or jobs remain queued

Open `/settings` and check Worker & Processing. Confirm `worker enabled` is on and the heartbeat is fresh. On restart, only processing jobs with stale heartbeats are requeued; fresh jobs are left untouched, and the unique transcript index prevents duplicate results.

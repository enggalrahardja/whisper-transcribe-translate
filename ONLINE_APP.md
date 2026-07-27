# Online Application Initialization

The existing CustomTkinter desktop application remains unchanged. The online application is initialized in separate directories.

## Structure

```text
apps/web/        Next.js application
services/api/    FastAPI service with MongoDB connectivity
storage/         Runtime media storage, created locally and not committed
```

## Next.js

```bash
cd apps/web
npm install
cp .env.example .env.local
npm run dev
```

Default URL: `http://127.0.0.1:3000`

## FastAPI

```bash
cd services/api
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

On Windows PowerShell, activate the virtual environment with:

```powershell
.\.venv\Scripts\Activate.ps1
```

Health endpoints:

```text
GET /health
GET /health/mongodb
```

## MongoDB

The default local connection is:

```text
mongodb://127.0.0.1:27017
```

Database:

```text
whisper_transcribe_translate
```

MongoDB must run as a native service. Docker is not used.

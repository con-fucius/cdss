# Kenya Clinical CDSS

Guideline-grounded clinical decision support system with a FastAPI backend,
React frontend, Postgres persistence, and prebuilt LanceDB guideline indexes.

## Prerequisites

- Python 3.11-3.13
- `uv`
- Node.js 22+
- `pnpm`
- Docker Desktop, if using Docker Compose

## Environment

Copy `.env.example` to `.env` if `.env` is not present.

The app defaults to Groq:

```env
QUERY_LLM_PROVIDER=groq
GROQ_API_KEY=
QUERY_LLM_MODEL=qwen/qwen3-32b
GROQ_REASONING_FORMAT=hidden
```

Set `GROQ_API_KEY` when you have the value. Without it, the backend runs in
retrieval-only mode and returns guideline passages without LLM synthesis.
`GROQ_REASONING_FORMAT=hidden` is deliberate for Qwen reasoning models: raw
reasoning must not appear in clinician-facing answers.

Chat retrieval uses chunk-level LanceDB retrieval by default:

```env
CDSS_CHAT_PAGEINDEX_MODE=off
CDSS_PAGEINDEX_CHAT_TIMEOUT_SECONDS=3
```

PageIndex remains available in the Knowledge Base and `/pageindex/*` endpoints.
Do not put PageIndex back into the chat hot path unless you are deliberately
evaluating `CDSS_CHAT_PAGEINDEX_MODE=auto` or `always`; it adds avoidable
latency and can pollute prompts with broad page summaries.

Puter remains available as a fallback:

```env
QUERY_LLM_PROVIDER=puter
PUTER_AUTH_TOKEN=
PUTER_MODEL=openai/gpt-4o-mini
PUTER_OPENAI_BASE_URL=https://api.puter.com/puterai/openai/v1
```

## Start Locally: Convenience Scripts

Use three PowerShell terminals from the repo root:

```powershell
cd D:\Projects\CDSS\HIV-agent
.\scripts\dev-services.ps1
```

```powershell
cd D:\Projects\CDSS\HIV-agent
.\scripts\dev-backend.ps1
```

```powershell
cd D:\Projects\CDSS\HIV-agent
.\scripts\dev-frontend.ps1
```

Open:

```text
http://127.0.0.1:5173
```

`dev-services.ps1` starts Postgres, runs migrations, then seeds the packaged
baseline evidence graph from `app/data/concepts`. `dev-backend.ps1` prefers
`.venv\Scripts\python.exe` if present and otherwise falls back to `uv`.
Backend reload is opt-in:

```powershell
$env:CDSS_BACKEND_RELOAD='1'
.\scripts\dev-backend.ps1
```

## Start Locally: Explicit uv + pnpm

Use this when you want direct control instead of helper scripts.

Terminal 1, Postgres and migrations:

```powershell
cd D:\Projects\CDSS\HIV-agent
docker compose up -d postgres
uv run python -m app.migrations
uv run python -m scripts.seed_evidence
```

Terminal 2, backend:

```powershell
cd D:\Projects\CDSS\HIV-agent
uv run uvicorn app.api:app --host 127.0.0.1 --port 8000
```

Terminal 3, frontend:

```powershell
cd D:\Projects\CDSS\HIV-agent\frontend
$env:VITE_DEV_PROXY_TARGET='http://127.0.0.1:8000'
pnpm install --frozen-lockfile
pnpm dev --host 127.0.0.1 --port 5173
```

Open:

```text
http://127.0.0.1:5173
```

## Start with Docker Compose

From the repo root:

```powershell
cd D:\Projects\CDSS\HIV-agent
docker compose up --build
```

Open:

```text
http://127.0.0.1:5173
```

Stop Docker without deleting Postgres or HuggingFace cache volumes:

```powershell
docker compose down
```

Docker bind-mounts the prebuilt LanceDB artifact at `app/lancedb`, so reviewers
do not need to reingest PDFs. The first Docker backend startup may spend several
minutes downloading the FastEmbed model into the Docker HuggingFace cache volume.
Providing `HF_TOKEN` in `.env` can improve HuggingFace rate limits.

## Verification

Backend health:

```powershell
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8000/health
```

Frontend proxy health:

```powershell
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:5173/health
```

Expected health can be `degraded` when `GROQ_API_KEY` is not set. That means
retrieval, LanceDB, and Postgres can be available while LLM synthesis is offline.

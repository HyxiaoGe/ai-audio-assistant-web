# AI Audio Assistant · Backend

[![CI](https://github.com/HyxiaoGe/ai-audio-assistant-web/actions/workflows/build-and-deploy.yml/badge.svg)](https://github.com/HyxiaoGe/ai-audio-assistant-web/actions/workflows/build-and-deploy.yml)
![Python](https://img.shields.io/badge/python-3.11%2B-blue)
![FastAPI](https://img.shields.io/badge/FastAPI-async-009688)

[中文](README.md)

Turn long audio/video content into **verifiable, reusable, discoverable** knowledge cards — transcripts, structured summaries, key points, action items, and illustrations.

This is the **backend** (FastAPI + Celery). The frontend is a separate Next.js app, `ai-audio-assistant-ui`; auth, prompts, and other shared concerns go through shared services.

## Features

All of the following are implemented in the codebase:

- **Multi-vendor ASR** — Tencent Cloud / Aliyun / Volcengine; speaker diarization, standard/fast variants, quota-aware scheduling (`ASRScheduler`). Audio is normalized to 16k mono mp3 before upload to sidestep per-file size limits.
- **Transcript polishing** — a `polishing` stage between transcription and summarization that mechanically fixes ASR errors (typos, terminology, punctuation) group-by-group, 1:1 segment count, original kept on failure.
- **Structured summaries** — 7 curated styles + `auto` detection; overview / key points / action items; **multi-model side-by-side compare**, version management with one-click activate, `regenerate`, SSE streaming.
- **Progressive illustrations** — after a summary completes, overview images are generated asynchronously (remote image-service); placeholder anchors are persisted first, then merged.
- **Transcript full-text search** — `GET /api/v1/tasks/search` using PostgreSQL `tsvector` + pg_jieba Chinese segmentation + GIN index + app-level highlighting. Lexical only, zero LLM cost (semantic / vector search is a **deliberate non-goal**).
- **YouTube integration** — OAuth connect, subscription sync into a "to-process inbox", channel browsing, starring, batch auto-transcribe, transcribe by link/keyword.
- **/discover (public)** — keyword search (yt-dlp `ytsearch`, zero quota, ≤6h cache), "what people search" trending, view-count harvested recommendations; all behind a discover kill-switch.
- **Public explore (anonymous)** — browse admin-published completed tasks with their transcripts/summaries, served via short-lived scoped media tickets.
- **Content governance** — CMS / Tencent TMS moderation (`search_query` / `ugc_publish` / `ugc_display` scenarios × `off` / `shadow` / `enforce` modes, fail-closed under enforce); manual blocklist/allowlist (channels, keywords, channel allowlist) and a channel-flag review queue.
- **Cost & quota** — admin per-user cost dashboard (¥ ASR+images / $ LLM, dual currency, not summed), ASR monthly quota management and alerts.
- **Platform** — WebSocket live progress, in-app notifications, user preferences, runtime config center, analytics, client error reporting, admin view of user transcripts.

## Architecture

| Component | Tech / Notes |
|-----------|--------------|
| API | FastAPI (container listens on `8000`, `docker-compose` maps to `8088`); unified envelope `{code, message, data, traceId}` |
| Async Worker | Celery (`--autoscale=8,1`) + Celery Beat scheduled jobs; Redis as broker/backend |
| Database | PostgreSQL (asyncpg) + Alembic migrations + pg_jieba Chinese segmentation |
| Object storage | MinIO (dev) / Tencent COS / Aliyun OSS / Volcengine TOS (multi-vendor in prod) |
| ASR | Tencent / Aliyun / Volcengine (vendor SDKs) |
| Text LLM | **Unified via LiteLLM Proxy**; the model catalog proxies LiteLLM directly, add/remove models only on the LiteLLM side |
| Image gen | Remote image-service (Gemini-family models) |
| Auth | RS256 JWT issued by auth-service, verified via **JWKS** (SSO) |
| Local editable deps | `prompthub-sdk`, `auth-client` (via `[tool.uv.sources]` pointing to workspace-parent dirs `../../prompthub`, `../../auth-service`) |

**Service selection (SmartFactory)**: external services register via `@register_service`; `SmartFactory` supports `health_first` (default) / `cost_first` / `performance_first` / `balanced`. Registered: ASR = `tencent` / `aliyun` / `volcengine`, storage = `minio` / `cos` / `oss` / `tos`, LLM = `proxy` (LiteLLM entry) + `image_service`. In prod text LLM always goes through `proxy`; real ASR routing is handled by the quota-aware `ASRScheduler`.

**Task pipeline**:

```
pending → extracting(10%) → transcribing(40%) → polishing(72%) → summarizing(82%) → completed(100%)
                                        │ any stage error
                                        ▼
                                      failed
```

A summary-text failure does not fail the whole task (the transcript is already usable); overview images are added asynchronously after `completed`.

## Quickstart

### Prerequisites

- **Python 3.11+** (base image `python:3.11-slim`; ruff syntax target `py312`)
- **PostgreSQL**, **Redis**, **FFmpeg**
- Package manager **uv** (single source of truth `pyproject.toml`; no `requirements.txt`)

### Local development (bare processes)

```bash
cp .env.example .env
uv sync --dev            # runtime + dev deps (pytest, ...); runtime only: uv sync
source .venv/bin/activate

# API (local --reload defaults to port 8000)
uvicorn app.main:app --reload

# Celery Worker
celery -A worker.celery_app worker --loglevel=info

# Celery Beat (scheduled jobs)
celery -A worker.celery_app beat --loglevel=info

# DB migrations
alembic upgrade head
```

> **Lint note**: the CI lint gate is **ruff**, but ruff is not in the `uv sync --dev` group (the dev group is legacy pytest/black/…). To match CI locally just run `uvx ruff` or `pip install ruff`.

### Docker

`docker-compose.yml` defines only 4 services: `api`, `worker`, `beat`, `migrate` (`migrate` is in the `manual` profile, run on demand).

> ⚠️ **Not a self-contained full stack**: PostgreSQL / Redis / MinIO are **not** in the compose file (they point to an external host), and it relies on a **pre-existing external network** `ai-audio-network`. Provide those external dependencies and the network before `up`.

```bash
docker-compose up -d              # api / worker / beat (needs external PG/Redis/MinIO + network)
docker compose run --rm migrate   # run migrations manually (alembic upgrade head)
```

The api container command is `uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload`, mapped `8088:8000` (i.e. `http://localhost:8088`).

## Quality gates & tests

```bash
ruff check app/ worker/ tests/          # lint — the CI gate
ruff format app/ worker/ tests/         # format
pytest tests/ -v                        # tests — the CI gate
pytest tests/ -v --cov=app --cov=worker # with coverage
```

CI (`.github/workflows/build-and-deploy.yml`, the build job) runs only **ruff + pytest**; the deploy job (`deploy-dev`) runs on `master` only. `black/isort/flake8/mypy/bandit` still appear in the `pyproject.toml` dev group but are **not** gates — ruff superseded them.

## Configuration

Config is defined centrally by a `pydantic-settings` `Settings` class (`app/config.py`), loaded from `.env`. **`app/config.py` is the authoritative definition; `.env.example` is a sample of common variables (not exhaustive)**; the tables below show key groups only. Real secrets are injected by a secret manager; `.env` is git-ignored — **never commit it**.

All settings have defaults; but when `APP_ENV=production`, a validator **requires** the following and the app **fails to start** if they are missing:

| Variable | Purpose |
|---|---|
| `FIELD_ENCRYPTION_KEY` | Fernet key for at-rest sensitive fields (e.g. OAuth tokens); comma-separated for rotation (first encrypts, all decrypt). Required in prod |
| `JWT_SECRET` | HS256 self-signing key for short-lived media / SSE tickets. Required in prod |

Other key groups (full list in `.env.example`):

| Group | Representative vars | Notes |
|-------|---------------------|-------|
| DB / Redis | `DATABASE_URL`, `REDIS_URL`, `DB_POOL_SIZE`, `DB_MAX_OVERFLOW` | required to run; when scaling workers, shrink the pool to protect the shared PG |
| Auth | `AUTH_SERVICE_URL`, `AUTH_SERVICE_INTERNAL_URL`, `AUTH_SERVICE_JWKS_URL` | JWKS prefers the internal LAN base to avoid public-tunnel tail latency |
| Object storage (pick one) | `MINIO_*` / `COS_*` / `OSS_*` / `TOS_*` | configure the group for the vendor you use |
| ASR (3 vendors) | `TENCENT_*` / `ALIYUN_*` / `VOLC_ASR_*` | auto-discovered by credentials; plus engine/diarization tuning |
| Text LLM | `LITELLM_BASE_URL`, `LITELLM_API_KEY`, `LITELLM_MODEL` | all chat/completion goes through LiteLLM Proxy |
| Image gen | `IMAGE_SERVICE_BASE_URL`, `IMAGE_SERVICE_API_KEY` | required for illustrations |
| Prompts | `PROMPTHUB_BASE_URL`, `PROMPTHUB_API_KEY` | sole live source of summary/image prompts, no local fallback; missing → related tasks fail at runtime (not enforced at startup) |
| Moderation | `MODERATION_*_MODE`, `MODERATION_API_KEY` | 3 scenarios × 3 modes, **default all `off`**; `enforce` needs the key |
| Discover / YouTube | `YOUTUBE_SEARCH_*`, `GOOGLE_CLIENT_ID/SECRET` | search timeouts/cache/rate-limits; OAuth creds |
| Rate limits / switches | `RATE_LIMIT_*`, `DEAD_TASK_SWEEP_ENABLED`, `CONFIG_CENTER_DB_ENABLED` | per-endpoint per-minute limits, sweeper & config-center switches |

> Note: some variables (`JWT_SECRET`, `DB_POOL_SIZE`/`DB_MAX_OVERFLOW`, `COS_*`, `MODERATION_*`, `RATE_LIMIT_*`, …) are not in `.env.example`; treat `app/config.py` as canonical. `ENABLE_DOCS` is read directly by `app/main.py`.

## API overview

All endpoints live under the `/api/v1` prefix (`app/api/v1/router.py`):

- `/tasks`, `/tasks/search` (transcript FTS), `/transcripts`, `/summaries`, `/summary-styles`
- `/upload`, `/media` (media tickets & byte stream), `/ws` (live-progress WebSocket)
- `/users`, `/notifications`, `/stats`, `/llm`, `/public` (anonymous explore)
- `/youtube` (subscriptions / discover / search / callbacks), `/asr/quotas`, `/asr/usage`
- `/configs` (config center), `/admin/*` (cost dashboard, task details, blocklist / allowlist / review queue)
- `/health`, `/readiness`, `/client-errors`

> Interactive docs (`/docs`, `/redoc`, `/openapi.json`) are gated by `ENABLE_DOCS` and **disabled by default** (compose sets it to `false`). To view locally: set `ENABLE_DOCS=true` before start, then open `http://localhost:8000/docs`.

## Repo layout

```
ai-audio-assistant-web/
├── app/                # FastAPI app
│   ├── api/            # routes (api/v1/*) & dependency injection (deps.py)
│   ├── core/           # SmartFactory / middleware / responses / exceptions / monitoring
│   ├── services/       # asr/ llm/ storage/ moderation/ youtube/ … external services
│   ├── models/         # SQLAlchemy models
│   ├── schemas/        # Pydantic request/response
│   ├── prompts/        # prompt template management
│   ├── i18n/           # error codes & localized copy
│   ├── config.py       # settings (pydantic-settings)
│   ├── db.py           # async session factory
│   └── main.py         # app assembly (create_app)
├── worker/             # Celery tasks & beat schedule
├── alembic/            # DB migrations
├── tests/              # pytest suite
├── scripts/            # ops / one-off scripts
├── loadtest/           # load-testing harness
└── docs/               # design & API docs
```

## Documentation

| Doc | Location | About |
|-----|----------|-------|
| Conventions | `CLAUDE.md` | layout/service conventions, SmartFactory, coding standards |
| API spec | `docs/API.md` | endpoints, request/response, error codes |
| Architecture | `docs/ARCH.md` | layering, request flow, SmartFactory |
| Decisions | `docs/ADR.md` | key technical ADRs |
| Product | `docs/PRD.md` | product goals & scope |
| FAQ | `docs/FAQ.md` | troubleshooting |
| RAG plan | `docs/RAG_PLAN.md` | semantic search plan (currently disabled on purpose) |
| Per-feature specs/plans | `docs/superpowers/specs/`, `docs/superpowers/plans/` | archived specs & implementation plans |
| Env sample | `.env.example` | full environment variables (authoritative) |

---

中文版:[README.md](README.md)

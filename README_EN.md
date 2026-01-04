# AI Audio Assistant Backend

[中文](README.md)

Backend for audio/video understanding with multi-vendor LLM/ASR/storage integrations.

## Features

- Transcription with timestamps
- Structured summaries (overview / key points / action items)
- Provider selection by health, cost, and performance
- Cost tracking and service monitoring

## Architecture

- API: FastAPI
- Worker: Celery + Redis
- Database: PostgreSQL
- Storage: MinIO (dev) / COS or OSS (prod)
- ASR: Tencent / Aliyun
- LLM: Doubao / Qwen / Moonshot / DeepSeek / OpenRouter

## Quickstart (local)

Prereqs: Python 3.11+, Redis, PostgreSQL, and FFmpeg.

```bash
cp .env.example .env
uv sync
source .venv/bin/activate

# API
uvicorn app.main:app --reload

# Worker
celery -A worker.celery_app worker --loglevel=info
```

For Docker usage, see `docker-compose.yml`.

## Configuration

- Environment variables: `.env.example`
- Multi-vendor usage & SmartFactory: `docs/QUICKSTART.md`
- Backend architecture: `docs/BACKEND_QUICKSTART.md`
- API docs: `docs/API.md`

## Tests

```bash
pytest tests/ -v
mypy app/ worker/
flake8 app/ worker/ tests/ --max-line-length=100
```

## Repo layout

- `app/`: FastAPI app
- `worker/`: Celery tasks
- `tests/`: pytest suite
- `docs/`: design & API docs
- `alembic/`: migrations

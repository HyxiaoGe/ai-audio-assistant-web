# Repository Guidelines

## Project Structure & Module Organization
- `app/`: FastAPI application code (routers, core middleware, services, models, schemas).
- `worker/`: Celery app and background task pipeline.
- `tests/`: Pytest suite organized by domain (e.g., `tests/services/`, `tests/worker/`).
- `alembic/`: Database migrations and configuration (`alembic.ini`).
- `docs/`: Architecture and API references.
- `data/`: Local data artifacts (do not commit secrets).
- Root configs: `pyproject.toml`, `docker-compose*.yml`, `STATUS.json`.

## Build, Test, and Development Commands
- `uv sync`: Install dependencies with uv.
- `source .venv/bin/activate`: Activate virtualenv.
- `uvicorn app.main:app --reload`: Run the API server locally.
- `celery -A worker.celery_app worker --loglevel=info`: Run background workers.
- `alembic upgrade head`: Apply database migrations.
- `pytest tests/`: Run the test suite.
- `pytest --cov=app --cov=worker tests/`: Run tests with coverage.
- `mypy app/`: Type checking.
- `black app/ worker/ tests/`: Format code.
- `isort app/ worker/ tests/`: Sort imports.
- `flake8 app/ worker/ tests/`: Lint.

## Coding Style & Naming Conventions
- Python 3.11; format with Black (line length 100) and Isort (Black profile).
- Keep functions typed; prefer explicit return types for service and API layers.
- Tests follow Pytest defaults: `test_*.py`, `Test*` classes, `test_*` functions.
- Use `snake_case` for modules/functions and `CamelCase` for classes.
 - Default to mainstream, industry-standard conventions. Avoid asking the user to choose between common patterns; pick the most standard option and proceed.

## Testing Guidelines
- Frameworks: `pytest`, `pytest-asyncio`, `pytest-cov`.
- Mark slow or integration tests with `@pytest.mark.slow` or `@pytest.mark.integration`.
- Default coverage reports are configured in `pyproject.toml`.

## Commit & Pull Request Guidelines
- Commit messages follow Conventional Commits: `feat:`, `refactor:`, `chore:`, `docs:`, `ci:`.
- PRs should include: concise summary, testing performed, and any migration or config notes.
- For API changes, mention updated endpoints and any docs updates in `docs/`.

## Configuration & Safety
- Configure secrets and providers via `.env.example` keys; never commit real secrets.
- Respect backend conventions (unified response helpers, `BusinessError`, DI with `Depends`).
- Avoid "flair" or showcasing skills. Default to implementing the most standard option; if not appropriate, do not mention it.
- When the user reports an issue, immediately check logs/service status instead of asking whether to do so.
- Only commit or push when the user explicitly instructs; do not ask repeatedly.
- After code changes, restart relevant services without asking.

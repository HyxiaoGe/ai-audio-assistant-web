# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**AI Audio Assistant Backend** - Transform any audio/video content into structured understanding (transcription + summary + key points).

This is a FastAPI-based backend service that processes audio/video content through ASR (Automatic Speech Recognition) and LLM services. The frontend is a separate Next.js repository.

## Development Commands

### Setup and Installation

```bash
# Install dependencies (using uv)
uv sync

# Activate virtual environment
source .venv/bin/activate

# Or if using traditional pip
pip install -e .
```

### Running the Application

```bash
# Start development server
uvicorn app.main:app --reload

# Start Celery worker for async tasks
celery -A worker.celery_app worker --loglevel=info

# View API documentation
open http://localhost:8000/docs
```

### Database Operations

```bash
# Run database migrations
alembic upgrade head

# Create a new migration after model changes
alembic revision --autogenerate -m "description of change"

# Rollback one migration
alembic downgrade -1
```

### Code Quality and Testing

```bash
# Type checking
mypy app/

# Format code
black app/ worker/ tests/

# Sort imports
isort app/ worker/ tests/

# Linting
flake8 app/ worker/ tests/

# Run tests
pytest tests/

# Run tests with coverage
pytest --cov=app --cov=worker tests/
```

## Architecture

### Core Design Principles

1. **Unified Response Format**: All API endpoints return `{"code": 0, "message": "成功", "data": {...}, "traceId": "..."}`
2. **Layered Architecture**: Middleware → Dependencies → Business Logic → Exception Handlers
3. **No Decorator Stacking**: Use dependency injection (`Depends`) instead of piling decorators
4. **Backend-Managed i18n**: Error messages are internationalized based on `Accept-Language` header

### Request Flow

```
Request → Middlewares (RequestID, Locale, Logging)
       → Dependencies (DB session, Auth, Validation)
       → Router Handler
       → Service Layer
       → Exception Handler → Unified Response
```

### Key Components

**Middleware Layer** (`app/core/middleware.py`):
- `RequestIDMiddleware`: Generates/propagates trace IDs
- `LocaleMiddleware`: Parses Accept-Language header
- `LoggingMiddleware`: Logs request details and timing

**Dependency Injection** (`app/api/deps.py`):
- `get_db`: Database session
- `get_current_user`: JWT authentication
- `get_task_by_id`: Resource retrieval with ownership validation

**Response Utilities** (`app/core/response.py`):
- `success(data, message)`: Standard success response
- `error(code, message, data)`: Standard error response (still HTTP 200)

**Exception Handling** (`app/core/exceptions.py`):
- `BusinessError`: Raised for business logic errors with error codes
- Global exception handler converts all exceptions to standard response format

**Error Codes** (`app/i18n/codes.py`):
- 0: Success
- 40000-40099: Parameter errors
- 40100-40199: Authentication errors
- 40300-40399: Authorization errors
- 40400-40499: Resource not found
- 50000-50099: System errors
- 51000-51999: Third-party service errors

### Service Architecture

**Factory Pattern for External Services**: All external services (ASR, LLM, Storage) use factory pattern for easy provider switching via environment variables.

- `app/services/asr/factory.py`: ASR service factory (Tencent/Aliyun)
- `app/services/llm/factory.py`: LLM service factory (Doubao/Qwen)
- `app/services/storage/factory.py`: Storage service factory (MinIO/S3)

**Async Task Processing**: Celery workers handle long-running tasks:
- `worker/tasks/process_audio.py`: Main audio processing pipeline
- `worker/tasks/download_youtube.py`: YouTube content download

### Database Models

All models inherit from `app/models/base.py` which provides:
- `id`: UUID primary key
- `created_at`, `updated_at`: Timestamps
- `deleted_at`: Soft delete support

Key models:
- `User`: User accounts and settings
- `Task`: Processing tasks with status tracking
- `Transcript`: Speech-to-text results with speaker diarization
- `Summary`: LLM-generated summaries (supports versioning)

### Task State Machine

```
pending → extracting → transcribing → summarizing → completed
   ↓          ↓              ↓              ↓
   └──────────┴──────────────┴──────────────┴──────→ failed
```

Progress mapping:
- pending: 0%
- extracting: 0-20%
- transcribing: 20-70%
- summarizing: 70-99%
- completed: 100%

## Code Quality Requirements

### Type Annotations (Required)

```python
# ✅ Correct
async def create_task(data: TaskCreate, user: User) -> Task:
    ...

# ❌ Wrong - no type annotations
def create_task(data, user):
    ...
```

### Exception Handling (Required)

```python
# ✅ Correct
try:
    result = await asr_service.transcribe(path)
except ASRError as e:
    raise BusinessError(ErrorCode.ASR_SERVICE_ERROR, reason=str(e))

# ❌ Wrong - empty except
try:
    ...
except Exception:
    pass
```

### Dependency Injection (Required)

```python
# ✅ Correct
@router.get("/tasks/{task_id}")
async def get_task(
    task: Task = Depends(get_task_by_id)  # Includes auth + ownership check
):
    return success(data=TaskResponse.model_validate(task))

# ❌ Wrong - decorator stacking
@router.get("/tasks/{task_id}")
@require_auth
@require_owner
async def get_task(...):
    ...
```

### Return Schema Objects (Required)

```python
# ✅ Correct
return success(data=TaskResponse.model_validate(task))

# ❌ Wrong - returning ORM model directly
return success(data=task)
```

## Environment Configuration

Key environment variables (see `.env.example`):

```bash
# Application
APP_ENV=development
DEBUG=true

# Database
DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/audio_assistant
REDIS_URL=redis://localhost:6379/0

# JWT (backend validates tokens issued by frontend)
JWT_SECRET=your-jwt-secret
JWT_ALGORITHM=HS256

# Storage
MINIO_ENDPOINT=localhost:9000
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=minioadmin
MINIO_BUCKET=audio-assistant
MINIO_USE_SSL=false

# ASR Provider (tencent | aliyun)
ASR_PROVIDER=tencent
TENCENT_SECRET_ID=xxx
TENCENT_SECRET_KEY=xxx

# LLM Provider (doubao | qwen)
LLM_PROVIDER=doubao
DOUBAO_API_KEY=xxx
DOUBAO_BASE_URL=https://ark.cn-beijing.volces.com/api/v3
DOUBAO_MODEL=doubao-1.5-pro-32k-250115
```

## Important Constraints

### What NOT to Change

- ❌ Don't add new databases (MongoDB, etc.)
- ❌ Don't add new message queues (RabbitMQ, etc.)
- ❌ Don't switch to GraphQL
- ❌ Don't add microservices architecture
- ❌ Don't delete database tables
- ❌ Don't change primary key types from UUID

### Single Task Focus

Complete one task at a time:
- ✅ Implement a single API endpoint
- ✅ Fix a specific bug
- ❌ Don't implement all API endpoints at once
- ❌ Don't refactor entire service layer

### Completion Checklist

Before considering a task complete:

```
[ ] Code uses unified response format (success/error helpers)
[ ] Errors use BusinessError, not HTTPException
[ ] All functions have type annotations
[ ] Dependencies injected via Depends()
[ ] Appropriate logging added
[ ] Schema objects defined and used
[ ] API appears correctly in /docs
[ ] mypy passes with no errors
[ ] Server starts without errors (uvicorn app.main:app)
```

## Project Status

Check `STATUS.json` for current development status and task tracking. This file is kept up-to-date with completed features and next tasks.

## Documentation References

- `docs/API.md`: Complete API endpoint specifications
- `docs/ARCH.md`: Detailed architecture design
- `docs/BACKEND_QUICKSTART.md`: Step-by-step backend development guide
- `STATUS.json`: Current project status and task tracking

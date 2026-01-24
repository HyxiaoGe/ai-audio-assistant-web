# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**AI Audio Assistant Backend** - Transform any audio/video content into structured understanding (transcription + summary + key points).

FastAPI-based backend service that processes audio/video content through ASR (Automatic Speech Recognition) and LLM services with intelligent multi-provider selection. The frontend is a separate Next.js repository.

## Prerequisites

- Python 3.11+
- PostgreSQL
- Redis
- FFmpeg (for audio extraction)
- Node.js + npm (for Mermaid CLI visual rendering)

## Development Commands

### Setup and Installation

```bash
# Install dependencies using uv (recommended)
uv sync --dev

# Activate virtual environment
source .venv/bin/activate

# Install pre-commit hooks (one-time setup)
pre-commit install
```

### Running the Application

```bash
# Using Docker Compose (recommended for full stack)
docker-compose up -d

# Or run services individually
uvicorn app.main:app --reload                    # API server
celery -A worker.celery_app worker --loglevel=info   # Worker

# View API documentation
open http://localhost:8000/docs
```

### Database Operations

```bash
# Run migrations
alembic upgrade head

# Create new migration
alembic revision --autogenerate -m "description"

# Rollback
alembic downgrade -1
```

### Testing

```bash
# Run all tests
pytest tests/ -v

# Run with coverage
pytest tests/ -v --cov=app --cov=worker

# Run a single test file
pytest tests/services/test_asr.py -v

# Run a single test function
pytest tests/services/test_asr.py::test_transcribe_audio -v

# Run tests by marker
pytest -m slow                         # Slow integration tests
pytest -m "not slow"                   # Fast unit tests only
```

### Code Quality

```bash
# Pre-commit runs automatically on git commit
# To run manually on all files:
pre-commit run --all-files

# Individual tools
black app/ worker/ tests/              # Format
isort app/ worker/ tests/              # Sort imports
flake8 app/ worker/ tests/             # Lint
mypy app/ worker/                      # Type check
```

## Architecture

### Core Design Principles

1. **Unified Response Format**: All endpoints return `{"code": 0, "message": "成功", "data": {...}, "traceId": "..."}`
2. **ServiceRegistry Pattern**: All external services (ASR, LLM, Storage) register via decorator pattern
3. **SmartFactory**: Intelligent service selection based on health, cost, performance, or balanced strategies
4. **No Decorator Stacking**: Use dependency injection (`Depends`) instead
5. **Backend-Managed i18n**: Messages internationalized via `Accept-Language` header

### Request Flow

```
Request → Middlewares (RequestID, Locale, Logging)
       → Dependencies (DB session, Auth, Validation)
       → Router Handler
       → Service Layer (via SmartFactory)
       → Exception Handler → Unified Response
```

### Service Architecture (SmartFactory Pattern)

**Core Components**:
- `app/core/registry.py`: ServiceRegistry - Centralized service discovery and instantiation
- `app/core/smart_factory.py`: SmartFactory - Intelligent service selection with multiple strategies
- `app/core/health_checker.py`: Health monitoring for all registered services
- `app/core/load_balancer.py`: Load distribution across healthy service instances
- `app/core/cost_optimizer.py`: Cost tracking and optimization
- `app/core/monitoring.py`: Metrics collection and service performance tracking

**Service Registration Pattern**:
```python
from app.core.registry import register_service, ServiceMetadata

@register_service(
    "llm",                           # service type
    "deepseek",                      # service name
    metadata=ServiceMetadata(
        display_name="深度求索",
        cost_per_1k_tokens=0.001,
        supports_streaming=True,
    )
)
class DeepSeekService(BaseLLMService):
    def __init__(self, model_id: Optional[str] = None):
        # model_id allows multi-model providers like OpenRouter
        self.model_id = model_id or "deepseek-chat"
```

**Service Selection Strategies**:
- `health_first`: Prefer healthiest services (default)
- `cost_first`: Minimize costs
- `performance_first`: Optimize for speed
- `balanced`: Balance all factors

**Using Services**:
```python
from app.core.smart_factory import SmartFactory

# Auto-select best LLM service
llm = await SmartFactory.get_service("llm")

# Select specific provider
llm = await SmartFactory.get_service("llm", provider="deepseek")

# For multi-model providers (OpenRouter)
llm = await SmartFactory.get_service(
    "llm",
    provider="openrouter",
    model_id="anthropic/claude-3.5-sonnet"
)

# Custom strategy
asr = await SmartFactory.get_service(
    "asr",
    strategy=SelectionStrategy.COST_FIRST
)
```

**Registered Services**:

ASR Providers (`app/services/asr/`):
- `tencent`: Tencent Cloud ASR
- `aliyun`: Alibaba Cloud ASR
- `volcengine`: Volcano Engine ASR

**ASR Scheduler** (`app/services/asr_scheduler.py`):
- Intelligent provider selection based on quota availability and platform free tiers
- Platform free tiers don't count against user quotas
- Automatic fallback when quotas are exhausted

LLM Providers (`app/services/llm/`):
- `doubao`: ByteDance Doubao (豆包)
- `qwen`: Alibaba Qwen (通义千问)
- `deepseek`: DeepSeek (深度求索)
- `moonshot`: Moonshot Kimi
- `openrouter`: OpenRouter (supports multiple models via `model_id`)

Storage Providers (`app/services/storage/`):
- `minio`: MinIO (S3-compatible)
- `cos`: Tencent Cloud COS
- `oss`: Alibaba Cloud OSS
- `tos`: Volcano Engine TOS

### Additional Features

**Prompt Management** (`app/prompts/`):
- Centralized prompt template management for LLM services
- Support for multiple locales and content styles
- Version control and A/B testing capabilities

**User Preferences API** (`app/api/v1/users.py`):
- GET/PATCH `/api/v1/users/me/preferences` - Manage user default settings
- Preferences stored in `users.settings` JSONB field
- Three-tier priority: request params > user preferences > system defaults
- Namespaces: `task_defaults`, `ui`, `rag`, `notifications`

**Configuration Center** (`app/api/v1/config_center.py`):
- Runtime service configuration management
- Dynamic enable/disable of ASR/LLM/Storage providers
- Configuration history tracking
- Admin-only access with audit logs

**ASR Quota Management** (`app/services/asr_quota_service.py`):
- Track ASR usage per provider
- Monthly quota limits and alerts
- Cost tracking and reporting
- API: `/api/v1/asr-quotas`

**RAG (Retrieval-Augmented Generation)** (`app/services/rag/`):
- Semantic chunking of transcripts
- Vector embeddings for intelligent search
- Context retrieval for enhanced summaries
- Models: `RagChunk` for storing embedded content

**Statistics & Analytics** (`app/api/v1/stats.py`):
- Task completion rates and processing times
- Service usage breakdown (ASR/LLM providers)
- Cost analytics per user/provider
- Error rate monitoring

### Key Components

**Middleware Layer** (`app/core/middleware.py`):
- `RequestIDMiddleware`: Trace ID generation/propagation
- `LocaleMiddleware`: Parse Accept-Language (supports region codes like zh-CN, en-US)
- `LoggingMiddleware`: Request timing and details

**Dependency Injection** (`app/api/deps.py`):
- `get_db`: Database session
- `get_current_user`: JWT authentication
- `get_task_by_id`: Resource retrieval with ownership validation

**Response Utilities** (`app/core/response.py`):
- `success(data, message)`: Standard success response
- `error(code, message, data)`: Standard error response (HTTP 200)

**Exception Handling** (`app/core/exceptions.py`):
- `BusinessError`: Business logic errors with error codes
- Global handler converts all exceptions to unified format

**Error Codes** (`app/i18n/codes.py`):
- 0: Success
- 40000-40099: Parameter errors
- 40100-40199: Authentication errors
- 40300-40399: Authorization errors
- 40400-40499: Resource not found
- 40900-40999: Business conflicts (e.g., quota exceeded)
- 50000-50099: System errors
- 51000-51999: Third-party service errors

### Database Models

All models inherit from `app/models/base.py`:
- `id`: UUID primary key
- `created_at`, `updated_at`: Timestamps
- `deleted_at`: Soft delete support

Key models:
- `User`: User accounts and settings
  - `settings`: JSONB field for user preferences
- `Task`: Processing tasks with status tracking
- `TaskStage`: Detailed stage tracking for each task phase
- `Transcript`: ASR results with speaker diarization
- `Summary`: LLM-generated summaries with versioning
  - `comparison_id`: Groups comparison results
  - `is_active`: Marks current active version
  - `model_used`: Records which LLM generated this version
- `RagChunk`: Vector embeddings for transcript chunks
- `AsrQuota`: Monthly ASR usage quotas per provider
- `LlmUsage`: LLM token usage tracking
- `ServiceConfig`: Runtime service configuration
- `ServiceConfigHistory`: Configuration change audit log
- `Notification`: User notifications (task completion, errors)

### Task State Machine

```
pending → extracting → transcribing → summarizing → completed
   ↓          ↓              ↓              ↓
   └──────────┴──────────────┴──────────────┴──────→ failed
```

Progress: pending(0%) → extracting(0-20%) → transcribing(20-70%) → summarizing(70-99%) → completed(100%)

### Multi-Model Comparison Feature

Users can compare LLM outputs side-by-side:

1. **Compare Request**: POST `/api/v1/summaries/{task_id}/compare`
   ```json
   {
     "summary_type": "overview",
     "models": [
       {"provider": "deepseek", "model_id": "deepseek-chat"},
       {"provider": "qwen", "model_id": "qwen-plus"},
       {"provider": "openrouter", "model_id": "anthropic/claude-3.5-sonnet"}
     ]
   }
   ```

2. **Streaming**: GET `/api/v1/summaries/{task_id}/compare/{comparison_id}/stream`
   - Returns SSE stream with events from all models
   - Each event includes `provider` and `model_id` for frontend to distinguish

3. **Activation**: POST `/api/v1/summaries/{task_id}/{summary_id}/activate`
   - Sets a comparison result as the current active version

## Code Quality Requirements

### Type Annotations (Required)

```python
# ✅ Correct
async def create_task(data: TaskCreate, user: User) -> Task:
    ...

# ❌ Wrong
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

# ❌ Wrong
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
    task: Task = Depends(get_task_by_id)  # Auth + ownership
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

# ❌ Wrong - ORM model directly
return success(data=task)
```

### Using SmartFactory (Required for External Services)

```python
# ✅ Correct - Use SmartFactory
llm_service = await SmartFactory.get_service("llm", provider="deepseek")

# ❌ Wrong - Direct instantiation
llm_service = DeepSeekService()
```

## Dependency Management

**Single Source of Truth**: `pyproject.toml`

All dependencies are defined in `pyproject.toml`:
- `[project.dependencies]`: Runtime dependencies
- `[dependency-groups.dev]`: Development tools (pytest, black, mypy, pre-commit)

**No `requirements.txt`**: We don't maintain a separate requirements.txt file.

**Installation Methods**:
```bash
# Using uv (recommended)
uv sync              # Install runtime deps
uv sync --dev        # Install with dev deps

# Using pip (fallback)
pip install -e .     # Install runtime deps
pip install -e .[dev]  # Install with dev deps
```

**Docker**: Dockerfile reads dependencies directly from `pyproject.toml` using Python's `tomllib`.

**CI/CD**: GitHub Actions extracts dependencies from `pyproject.toml` dynamically.

## Pre-commit and CI/CD

### Pre-commit Hooks (Local)

Runs automatically on `git commit`:
- ✅ Auto-fix: trailing whitespace, EOF newlines, Black formatting, isort
- ⚠️ Check-only: Flake8, mypy, Bandit, YAML/JSON syntax

Manual run: `pre-commit run --all-files`

### GitHub Actions (CI)

Triggers on push/PR to master/main/develop:
1. **Pre-commit checks** - All hooks (check-only, no fixes)
2. **Lint & Type check** - Black, isort, Flake8, mypy
3. **Security scan** - Bandit
4. **Tests** - pytest with coverage

Configuration:
- `.pre-commit-config.yaml`: Pre-commit hooks
- `.github/workflows/ci.yml`: GitHub Actions
- `pyproject.toml`: Tool settings (Black, isort, mypy, pytest)

## API Endpoints Summary

Key endpoint groups:
- `/api/v1/auth` - Authentication (token refresh, logout)
- `/api/v1/users` - User management and preferences
- `/api/v1/upload` - Presigned URL generation
- `/api/v1/tasks` - Task CRUD and listing
- `/api/v1/transcripts` - Transcript retrieval and editing
- `/api/v1/summaries` - Summary generation and comparison
- `/api/v1/llm` - Available LLM models listing
- `/api/v1/config-center` - Service configuration (admin)
- `/api/v1/asr-quotas` - ASR quota management
- `/api/v1/stats` - Usage statistics and analytics
- `/api/v1/notifications` - User notifications
- `/api/v1/ws` - WebSocket for real-time updates
- `/api/v1/media` - Media file streaming

## Environment Configuration

Key variables (see `.env.example`):

```bash
# Database
DATABASE_URL=postgresql+asyncpg://user:pass@localhost:5432/audio_assistant
REDIS_URL=redis://localhost:6379/0

# JWT
JWT_SECRET=your-secret
JWT_ALGORITHM=HS256

# Storage (choose one)
MINIO_ENDPOINT=localhost:9000
MINIO_ACCESS_KEY=minioadmin
MINIO_SECRET_KEY=minioadmin

# ASR (configure providers you want to use)
TENCENT_SECRET_ID=xxx
TENCENT_SECRET_KEY=xxx
ALIYUN_ACCESS_KEY_ID=xxx
ALIYUN_ACCESS_KEY_SECRET=xxx
VOLCENGINE_ACCESS_KEY_ID=xxx
VOLCENGINE_SECRET_ACCESS_KEY=xxx

# LLM (configure providers you want to use)
DOUBAO_API_KEY=xxx
QWEN_API_KEY=xxx
DEEPSEEK_API_KEY=xxx
MOONSHOT_API_KEY=xxx
OPENROUTER_API_KEY=xxx
```

**Provider Selection**: SmartFactory automatically discovers available services based on configured credentials. No need to set `ASR_PROVIDER` or `LLM_PROVIDER` environment variables.

## Important Constraints

### What NOT to Change

- ❌ Don't add new databases (MongoDB, etc.)
- ❌ Don't add new message queues (RabbitMQ, Kafka, etc.)
- ❌ Don't switch to GraphQL
- ❌ Don't add microservices architecture
- ❌ Don't delete database tables
- ❌ Don't change primary key types from UUID
- ❌ Don't create `requirements.txt` - use `pyproject.toml` only

### Adding New Service Providers

To add a new LLM/ASR/Storage provider:

1. Create service class in `app/services/{type}/{provider}.py`
2. Inherit from base class (`BaseLLMService`, `BaseASRService`, `BaseStorageService`)
3. Use `@register_service()` decorator with metadata
4. Add config to `app/services/{type}/configs.py`
5. Add credentials to `.env.example`
6. Update display names in `app/api/v1/llm.py` (for LLM providers)

Example:
```python
@register_service(
    "llm",
    "new_provider",
    metadata=ServiceMetadata(
        display_name="New Provider",
        cost_per_1k_tokens=0.002,
        supports_streaming=True,
    )
)
class NewProviderService(BaseLLMService):
    ...
```

### Commit Message Convention

Follow Conventional Commits format:
- `feat:` New feature
- `fix:` Bug fix
- `refactor:` Code refactoring
- `chore:` Maintenance tasks
- `docs:` Documentation
- `ci:` CI/CD changes
- `test:` Test additions/changes

### Agent Behavior Guidelines

- When the user reports an issue, immediately check logs/service status instead of asking whether to do so
- Only commit or push when the user explicitly instructs; do not ask repeatedly
- After code changes, restart relevant services without asking
- Default to mainstream, industry-standard conventions; avoid asking users to choose between common patterns

### Single Task Focus

Complete one task at a time:
- ✅ Implement a single API endpoint
- ✅ Fix a specific bug
- ❌ Don't implement all endpoints at once
- ❌ Don't refactor entire service layer

### Completion Checklist

```
[ ] Code uses unified response format (success/error helpers)
[ ] Errors use BusinessError, not HTTPException
[ ] All functions have type annotations
[ ] Dependencies injected via Depends()
[ ] External services accessed via SmartFactory
[ ] Monitoring decorators applied (@monitor)
[ ] Fault tolerance added (retry, circuit breaker)
[ ] Appropriate logging added
[ ] Schema objects defined and used
[ ] Pre-commit passes (run locally before commit)
[ ] API appears correctly in /docs
[ ] Server starts without errors
```

## Monitoring and Observability

**Monitoring System** (`app/core/monitoring.py`):
- Automatic metrics collection via `@monitor` decorator
- Track latency, error rates, and throughput per service
- Service health scores and availability tracking

**Fault Tolerance** (`app/core/fault_tolerance.py`):
- Circuit breaker pattern for failing services
- Exponential backoff retry with jitter
- Configurable failure thresholds and timeouts

**Health Checks**:
- Service-level health checks via `health_check()` method
- Automatic service degradation on repeated failures
- Health scores factor into SmartFactory selection

## Documentation References

- `README.md`: Quick start and overview (Chinese)
- `README_EN.md`: English version
- `.pre-commit-setup.md`: Pre-commit usage guide
- `docs/API.md`: API specifications (if exists)
- `docs/ARCH.md`: Architecture details (if exists)
- Interactive API docs: http://localhost:8000/docs

---

## Visual Summaries (v1.3+)

Multi-modal visual summary generation using Mermaid diagrams.

### Visual Types

- **Mindmap** (思维导图): Hierarchical concept maps
- **Timeline** (时间轴): Chronological event sequences
- **Flowchart** (流程图): Process flows

### Key Components

- `app/prompts/templates/visual/`: Prompt templates by locale and content style
- `worker/tasks/summary_visual_generator.py`: Generation logic
- `worker/tasks/process_visual_summary.py`: Celery task
- `app/api/v1/summaries.py`: API endpoints (`POST /{task_id}/visual`, `GET /{task_id}/visual/{type}`)

### Image Rendering

Uses Mermaid CLI (`mmdc`) to render diagrams:
- Install: `npm install -g @mermaid-js/mermaid-cli`
- Falls back to Mermaid syntax if rendering fails

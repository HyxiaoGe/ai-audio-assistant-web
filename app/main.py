import logging
import os

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DBAPIError

from app.api.v1.router import api_router
from app.config import settings
from app.core import litellm_health
from app.core.config_manager import ConfigManager
from app.core.exceptions import BusinessError
from app.core.i18n import get_message
from app.core.logging_config import configure_logging
from app.core.middleware import (
    LocaleMiddleware,
    LoggingMiddleware,
    PathExcludingGZipMiddleware,
    RequestIDMiddleware,
    UserContextMiddleware,
    VersionHeaderMiddleware,
)
from app.core.monitoring import MonitoringSystem
from app.core.response import error, get_request_id, reset_request_id, set_request_id
from app.core.smart_factory import SelectionStrategy, SmartFactory, SmartFactoryConfig
from app.db import async_session_factory
from app.i18n.codes import ErrorCode
from app.services.asr import (
    aliyun,  # noqa: F401
    tencent,  # noqa: F401
    volcengine,  # noqa: F401
)
from app.services.asr import configs as asr_configs  # noqa: F401

# 导入所有服务模块以触发 @register_service 装饰器
# 必须在模块顶层导入，而不是在函数内部，这样装饰器才会正确执行
from app.services.llm import configs as llm_configs  # noqa: F401
from app.services.llm import image_service as _llm_image_service  # noqa: F401
from app.services.llm import proxy as _llm_proxy  # noqa: F401
from app.services.storage import configs as storage_configs  # noqa: F401
from app.services.storage import cos, minio, oss, tos  # noqa: F401

logger = logging.getLogger(__name__)


def _http_status_error_code(status_code: int) -> ErrorCode:
    if status_code == 401:
        return ErrorCode.AUTH_TOKEN_INVALID
    if status_code == 403:
        return ErrorCode.PERMISSION_DENIED
    if status_code == 404:
        return ErrorCode.RESOURCE_NOT_FOUND
    if 400 <= status_code < 500:
        return ErrorCode.INVALID_PARAMETER
    return ErrorCode.INTERNAL_SERVER_ERROR


def _http_exception_message(exc: HTTPException, locale: str, code: ErrorCode) -> str:
    if isinstance(exc.detail, str) and exc.detail:
        return exc.detail
    return get_message(code, locale, detail=str(exc.detail))


def _is_media_stream_request(request: Request) -> bool:
    """媒体字节流：GET /api/v1/media/<key>（POST /media/ticket 不在此列）。

    这些 URL 由浏览器 <audio>/<img> 直连、不经 api-client 解析 envelope，需要真实
    HTTP 状态码才能触发其 error 事件，进而由前端刷新短票并重试。其余端点一律保持
    统一 200+envelope（前端按 code 字段判定）。
    """
    return request.method == "GET" and request.url.path.startswith("/api/v1/media/")


def _media_http_status(code: ErrorCode) -> int:
    """把统一错误码按区间映射为真实 HTTP 状态码（仅用于媒体字节流路径）。"""
    value = int(code)
    if 40100 <= value < 40200:  # 鉴权（未提供/失效/无效）
        return 401
    if 40300 <= value < 40400:  # 越权
        return 403
    if 40400 <= value < 40500:  # 资源不存在
        return 404
    if 51000 <= value < 52000:  # 三方/存储错误
        return 502
    if 40000 <= value < 41000:  # 参数/冲突类
        return 400
    return 500


async def business_error_handler(request: Request, exc: BusinessError) -> JSONResponse:
    locale = getattr(request.state, "locale", "zh")
    message = get_message(exc.code, locale, **exc.kwargs)
    if _is_media_stream_request(request):
        return error(exc.code.value, message, status_code=_media_http_status(exc.code))
    return error(exc.code.value, message)


async def database_error_handler(request: Request, exc: DBAPIError) -> JSONResponse:
    locale = getattr(request.state, "locale", "zh")
    # 媒体字节流路径（get_media_user → _resolve_user 的 db.get/flush 可能抛 DBAPIError）
    # 同样需要真实 HTTP 状态码，否则 <audio> 收到 200+JSON 无法触发 error 事件刷票重试。
    is_media = _is_media_stream_request(request)
    if "invalid UUID" in str(exc):
        # 无效的ID直接当作资源不存在处理，用户不需要知道ID格式问题
        code = ErrorCode.TASK_NOT_FOUND
        message = get_message(code, locale)
        status_code = _media_http_status(code) if is_media else 200
        return error(code.value, message, status_code=status_code)
    code = ErrorCode.DATABASE_SERVICE_ERROR
    message = get_message(code, locale)
    status_code = _media_http_status(code) if is_media else 200
    return error(code.value, message, status_code=status_code)


def create_app() -> FastAPI:
    _enable_docs = os.getenv("ENABLE_DOCS", "false").lower() == "true"
    app = FastAPI(
        title="AI Audio Assistant API",
        version="0.1.0",
        docs_url="/docs" if _enable_docs else None,
        redoc_url="/redoc" if _enable_docs else None,
        openapi_url="/openapi.json" if _enable_docs else None,
    )

    # CORS configuration
    cors_origins = [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]
    if settings.CORS_ORIGINS:
        cors_origins.extend(origin.strip() for origin in settings.CORS_ORIGINS.split(",") if origin.strip())
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["Content-Range", "Accept-Ranges", "Content-Length", "Content-Type", "X-App-Version"],
    )

    app.include_router(api_router)

    app.add_middleware(RequestIDMiddleware)
    app.add_middleware(LocaleMiddleware)
    app.add_middleware(UserContextMiddleware)
    app.add_middleware(LoggingMiddleware)
    app.add_middleware(VersionHeaderMiddleware)
    # GZip 放最外层(add_middleware 后加=外层):origin→CF 边缘段实测裸奔
    # (260KB 转写 raw 无 content-encoding),压缩典型省 ~100ms/次、最大转写 ~300ms。
    # 媒体字节流路径整体排除(Range/206 与 gzip 语义错位,音频/WebP 再压无益);
    # SSE 由锁定的 starlette 0.50.0 默认按 text/event-stream 排除(详见中间件 docstring)。
    app.add_middleware(
        PathExcludingGZipMiddleware,
        exclude_path_prefixes=("/api/v1/media", "/api/v1/summaries/images"),
        minimum_size=1024,
    )

    @app.on_event("startup")
    async def startup_event() -> None:
        """Initialize services on application startup."""
        configure_logging()
        SmartFactory.configure(
            SmartFactoryConfig(
                default_strategy=SelectionStrategy.HEALTH_FIRST,
                enable_monitoring=True,
                enable_fault_tolerance=True,
            )
        )
        MonitoringSystem.get_instance().start()
        ConfigManager.configure_db(async_session_factory, cache_ttl_seconds=settings.CONFIG_CENTER_CACHE_TTL)
        if settings.CONFIG_CENTER_DB_ENABLED:
            await ConfigManager.refresh_from_db()
        await litellm_health.start()

    @app.on_event("shutdown")
    async def shutdown_event() -> None:
        await litellm_health.stop()
        MonitoringSystem.get_instance().stop()

    app.add_exception_handler(BusinessError, business_error_handler)

    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
        locale = getattr(request.state, "locale", "zh")
        code = _http_status_error_code(exc.status_code)
        message = _http_exception_message(exc, locale, code)
        return error(code.value, message)

    app.add_exception_handler(DBAPIError, database_error_handler)

    @app.exception_handler(Exception)
    async def general_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        # 异常处理器跑在 RequestIDMiddleware 之外的 ServerErrorMiddleware 层,中间件的 finally
        # 可能已把 request_id contextvar reset 掉了;故从 request.state.trace_id 取回并临时重置,
        # 使 500 的异常日志行与返回给客户端的 traceId 用同一个 id(可关联排障)。
        trace_id = getattr(request.state, "trace_id", None) or get_request_id()
        token = set_request_id(trace_id)
        try:
            logger.exception(
                "Unhandled exception in %s %s [trace_id=%s]: %s: %s",
                request.method,
                request.url.path,
                trace_id,
                exc.__class__.__name__,
                exc,
            )

            locale = getattr(request.state, "locale", "zh")
            message = get_message(ErrorCode.INTERNAL_SERVER_ERROR, locale)
            status_code = 500 if _is_media_stream_request(request) else 200
            return error(ErrorCode.INTERNAL_SERVER_ERROR.value, message, status_code=status_code)
        finally:
            reset_request_id(token)

    return app


app = create_app()
